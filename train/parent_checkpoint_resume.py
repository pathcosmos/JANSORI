"""External-parent resume hooks for the unchanged native training loop.

This module does not authorize, launch, copy, retire or modify checkpoints.
The caller must bind these inputs in its execution contract, protect the parent
from retention, stop conflicting workers, and verify the actual distributed
handoff before accepting a migrated production run. No live entry imports it yet.
"""
from copy import deepcopy
import json
from pathlib import Path

from scripts.tiered_checkpoint_retention import inventory
from train.final_resume_probe import file_stats
from train.horizon_transition import build_candidate, validate_transition
from train.runtime import complete_checkpoints, validate_resume


def canonical(path):
    path = Path(path).absolute()
    if path != path.resolve() or any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError('Resume path must be canonical and contain no symlinks')
    return path


class ExternalParentResume:
    """Select an external parent once; subsequent scans only see child outputs.

    The native loop uses the same scan function for initial resume and pruning.
    Returning the parent on later scans could expose it to child-run deletion.
    This explicit one-use selection avoids that, including failed first saves.
    A process restart constructs a fresh instance. If a complete child exists,
    ordinary exact-identity resume takes precedence and the parent is not read.
    """

    def __init__(self, *, parent_checkpoint, destination, parent_metadata,
                 child_contract, parent_proof, verified_parent_stats, execution_binding=None):
        self.parent = canonical(parent_checkpoint)
        self.destination = canonical(destination)
        if (self.parent == self.destination or self.parent.is_relative_to(self.destination)
                or self.destination.is_relative_to(self.parent)):
            raise ValueError('Parent and child checkpoint scopes overlap')
        self.meta = deepcopy(parent_metadata)
        self.contract = deepcopy(child_contract)
        self.proof = deepcopy(parent_proof)
        self.stats = deepcopy(verified_parent_stats)
        self.execution_binding = deepcopy(execution_binding)
        self.transition = self.validate_transition()
        if (self.parent.name != f"ckpt_{self.meta['step']:07d}" or
                self.proof.get('step') != self.meta['step'] or
                self.proof.get('training_contract_id') != self.meta['training_contract']['id']):
            raise ValueError('Parent certificate and metadata identity differ')
        self.scanned = False
        self.parent_selected = False

    def validate_transition(self):
        if self.execution_binding is not None:
            from train.horizon_worker_contract import validate_executable_candidate
            return validate_executable_candidate(self.meta, self.contract, self.execution_binding)
        candidate, _ = build_candidate(self.meta, self.contract['config']['max_steps'])
        if candidate != self.contract:
            raise ValueError('Child contract differs from the reviewed horizon change')
        return validate_transition(self.meta['training_contract'], self.contract, self.meta['step'])

    def verify_parent(self):
        # A controller supplies a freshly SHA-verified stat snapshot bound in
        # its handoff contract. Each rank checks it without rehashing36GiB.
        if file_stats(self.parent) != self.stats:
            raise ValueError('Parent changed after content verification')
        inventory(self.parent, self.proof)
        if json.loads((self.parent/'meta.json').read_text()) != self.meta:
            raise ValueError('Parent metadata differs')
        if file_stats(self.parent) != self.stats:
            raise ValueError('Parent changed during verification')

    def checkpoints(self, directory):
        if canonical(directory) != self.destination:
            raise ValueError('Checkpoint scan outside the bound child output')
        children = complete_checkpoints(self.destination)
        for child in children:
            canonical(child)
            if child.parent != self.destination:
                raise ValueError('Child checkpoint outside managed namespace')
        if self.scanned:
            return children  # Never expose external parent to native pruning.
        if children:
            self.scanned = True
            return children
        self.verify_parent()
        self.scanned = True
        self.parent_selected = True
        return [self.parent]

    def validate(self, metadata, contract):
        if not self.scanned:
            raise ValueError('Resume source was not selected')
        if contract != self.contract:
            raise ValueError('Unbound child training contract')
        if self.parent_selected:
            self.verify_parent()
            if metadata != self.meta:
                raise ValueError('Selected parent metadata differs')
            return self.validate_transition()
        # After a child save/restart this is an ordinary native exact resume.
        validate_resume(metadata, contract)
        if type(metadata.get('step')) is not int or metadata['step'] <= self.meta['step']:
            raise ValueError('Child checkpoint does not advance the parent prefix')
        if metadata['step'] > contract['config']['max_steps']:
            raise ValueError('Child checkpoint exceeds the new target')
        return {'mode': 'exact_child_resume', 'contract_id': contract['id']}

    def load(self, loader, *args, **kwargs):
        """Wrap DCP loading with source scope and before/after content-stat guards."""
        if not self.scanned or 'checkpoint_id' not in kwargs:
            raise ValueError('Loading requires a selected, explicit checkpoint')
        path = canonical(kwargs['checkpoint_id'])
        if path == self.parent:
            if not self.parent_selected:
                raise ValueError('External parent was not selected for this attempt')
            self.verify_parent()
            result = loader(*args, **kwargs)
            self.verify_parent()
            return result
        if path.parent != self.destination or path not in complete_checkpoints(self.destination):
            raise ValueError('Load outside the completed child checkpoint namespace')
        return loader(*args, **kwargs)
