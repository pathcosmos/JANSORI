"""Own-checkpoint-only resume hooks, pending actual8GPU runtime validation.

Retains the original transition identity checks while making any fallback to
the old protected parent an error. No production entry imports this module yet.
"""
from copy import deepcopy
import json
from pathlib import Path

from common.data_contracts import resolve
from scripts.tiered_checkpoint_retention import inventory
from train.extended_parent_checkpoint_resume import ExtendedParentResume
from train.parent_checkpoint_resume import canonical
from train.final_resume_probe import file_stats


class IndependentChildResume(ExtendedParentResume):
    def __init__(self, *, child_checkpoint, child_proof, verified_child_stats, **kwargs):
        super().__init__(**kwargs)
        self.child = canonical(child_checkpoint)
        self.child_proof = deepcopy(child_proof)
        self.child_stats = deepcopy(verified_child_stats)
        if (self.child.parent != self.destination or
                self.child.stat().st_dev != self.destination.parent.stat().st_dev or
                self.child_proof.get('status') != 'passed_main_checkpoint_storage' or
                canonical(resolve(self.child_proof['checkpoint'])) != self.child or
                self.child_proof['training_contract_id'] != self.contract['id'] or
                self.child.name != f"ckpt_{self.child_proof['step']:07d}" or
                not self.meta['step'] < self.child_proof['step'] <= 57000):
            raise ValueError('Independent child certificate/scope differs')
        self.verify_child()
        self.child_meta = json.loads((self.child/'meta.json').read_text())

    def verify_parent(self):
        raise ValueError('Protected-parent fallback is disabled; require a certified own NFS checkpoint')

    def verify_child(self):
        if file_stats(self.child) != self.child_stats:
            raise ValueError('Independent child changed after SHA verification')
        inventory(self.child, self.child_proof)
        if file_stats(self.child) != self.child_stats:
            raise ValueError('Independent child changed during verification')

    def checkpoints(self, directory):
        first = not self.scanned
        result = super().checkpoints(directory)
        if first:
            if not result or result[-1] != self.child:
                raise ValueError('Latest completed child differs from admitted certificate')
            self.verify_child()
        return result

    def validate(self, metadata, contract):
        self.verify_child()
        if metadata != self.child_meta:
            raise ValueError('Independent child metadata differs')
        return super().validate(metadata, contract)

    def load(self, loader, *args, **kwargs):
        if (not self.scanned or self.parent_selected or 'checkpoint_id' not in kwargs or
                canonical(kwargs['checkpoint_id']) != self.child):
            raise ValueError('Load requires the exact admitted own NFS checkpoint')
        self.verify_child()
        result = loader(*args, **kwargs)
        self.verify_child()
        return result
