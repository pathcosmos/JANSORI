"""Read-only final50k accounting without opening ancestor checkpoint payloads.

This is a separate completion gate, not a replacement for the frozen producer
or its final consumers. Tensor SHA, source exit, isolated actual8GPU restoration
and parent retirement remain separate requirements.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.data_contracts import artifact_path, identity, resolve, sha256_file
from scripts.audit_main_training import Snapshot
from scripts.audit_horizon_training import audit_range_trace
from scripts.audit_budget_horizon import aggregate_budget_lineage
from scripts.budget_final_lineage import bound_intervals
from scripts.budget_horizon_lineage import FILES, calculate, records
from scripts.inspect_budget_completed_identity import inspect as inspect_identity
from scripts.tiered_checkpoint_retention import exact_path

CODE = ['scripts/audit_budget_completed_ledgers.py',
        'scripts/inspect_budget_completed_identity.py', 'scripts/budget_completed_identity.py',
        'scripts/audit_main_training.py', 'scripts/audit_horizon_training.py',
        'scripts/audit_budget_horizon.py', 'scripts/budget_final_lineage.py',
        'scripts/budget_horizon_lineage.py', 'scripts/extended_horizon_lineage.py']


class LedgerReader:
    """Pin the first snapshot and deny all ancestor/payload reads."""
    def __init__(self, denied):
        self.denied = [exact_path(resolve(p)) for p in denied]
        self.snapshot = Snapshot()
        self.read_paths = set()

    def path(self, path):
        p = exact_path(resolve(path))
        if (p.suffix == '.distcp' or 'parent_checkpoint' in p.parts or
                any(p == d or p.is_relative_to(d) for d in self.denied)):
            raise ValueError('Ancestor checkpoint/payload read forbidden')
        return p

    def read(self, path, *, lines=False):
        p = self.path(path)
        if p in self.read_paths:
            raise ValueError('Repeated ledger read would replace first snapshot')
        if p in self.snapshot.stats and self.snapshot.stamp(p) != self.snapshot.stats[p]:
            raise ValueError('Bound ledger changed before reading')
        self.read_paths.add(p)
        return self.snapshot.read(p, lines=lines, require_newline=lines)

    def bind(self, path, digest):
        p = self.path(path)
        before = self.snapshot.stamp(p)
        if sha256_file(p) != digest or self.snapshot.stamp(p) != before:
            raise ValueError('Final audit input byte binding changed')
        if p in self.snapshot.stats and self.snapshot.stats[p] != before:
            raise ValueError('Final audit input changed after first snapshot')
        self.snapshot.stats[p] = before
        self.snapshot.bindings[artifact_path(p)] = digest


def completed_counts(**kwargs):
    """Always require the entire final interval and eight successful receipts."""
    if kwargs.get('through_step') != 50000 or type(kwargs.get('through_step')) is not int:
        raise ValueError('Completed audit requires exactly50000 steps')
    result = aggregate_budget_lineage(**kwargs)
    bound_intervals(result['segments'])
    if (result['committed_step_rank_pairs'] != 400000 or
            result['committed_tokens'] != 104857600000 or
            result['successful_target_ranks'] != list(range(8))):
        raise ValueError('Final8rank unique coverage differs')
    return result


def audit_completed(main):
    main = exact_path(resolve(main))
    verified = inspect_identity(main, final=True)
    # The initial identity admission may read contract metadata, never parents.
    # Rebind every admission byte before collecting accounting evidence.
    contract = json.loads((main/'contract.json').read_text())
    reader = LedgerReader([resolve(d['run'])/'parent_checkpoint'
                           for d in contract['retention_branches']])
    code = {p:sha256_file(resolve(p)) for p in CODE}
    for p, digest in code.items():
        reader.bind(p, digest)
    # Read the documents first; bind() below keeps these first snapshots.
    c = reader.read(main/'contract.json')
    config = reader.read(main/'main.json')
    child = reader.read(main/'training/training_contract.json')
    receipt = reader.read(main/'parent_handoff.json')
    for path, digest in verified['metadata_inputs'].items():
        reader.bind(path, digest)
    probe = reader.read(main/'probe/probe.json')
    prompts = reader.read(resolve(c['prompts']), lines=True)
    mix = reader.read(resolve(config['mix']))
    if (sha256_file(main/'probe/probe.json') != child['monitored_evaluation']['probe_sha256'] or
            sha256_file(resolve(c['prompts'])) != child['monitored_evaluation']['prompts_sha256'] or
            set(mix['sources']) != set(child['source_order']) or
            len(probe['groups']) != len(child['source_order']) or
            {g['source'] for g in probe['groups']} != set(child['source_order'])):
        raise ValueError('Final probe/prompt/source identity differs')
    manifest = reader.read(main/'lineage/manifest.json')
    parent = receipt['parent_metadata']
    if (set(manifest['files']) != FILES or
            manifest.get('status') != 'prepared_three_prefix_50k_lineage' or
            manifest.get('version') != 1 or manifest.get('parent_step') != parent['step'] or
            manifest.get('parent_contract_id') != parent['training_contract']['id'] or
            manifest.get('probe_id') != identity(probe) or
            manifest.get('prompts_id') != identity(prompts)):
        raise ValueError('Exact sealed prefix identity required')
    reader.bind(main/'lineage/manifest.json', c['budget_execution']['parent_ledger_manifest_sha256'])
    payload = {}
    for name, digest in manifest['files'].items():
        p = main/'lineage'/name
        payload[name] = reader.read(p, lines=name.endswith('.jsonl'))
        reader.bind(p, digest)
    if payload['parent_metadata.json'] != parent:
        raise ValueError('Sealed parent metadata differs')
    prefix = calculate(payload, probe, prompts)
    if prefix != payload['audit.json']:
        raise ValueError('Recomputed prefix differs from sealed audit')
    archived = records(payload)
    paths = sorted((main/'ledgers').glob('*.json'))
    attempts, seen = [], set()
    for p in paths:
        match = re.fullmatch(r'(.+)-rank([0-7])\.json', p.name)
        stat = reader.path(p).stat()
        inode = (stat.st_dev, stat.st_ino)
        if not match or inode in seen:
            raise ValueError('Invalid or aliased final ledger')
        seen.add(inode)
        row = reader.read(p)
        if row['rank'] != int(match[2]):
            raise ValueError('Ledger rank filename differs')
        attempts.append((match[1], row))
    extension = payload['extension_metadata.json']
    counts = completed_counts(
        original=payload['previous_lineage.json']['original_contract.json'],
        current=extension['training_contract'], restored=parent['training_contract'], child=child,
        original_records=archived['original'], current_records=archived['current'],
        restored_records=archived['restored'], child_records=attempts,
        extension_metadata=extension, parent_metadata=parent, through_step=50000)
    trace = audit_range_trace(reader.read(main/'training/log.jsonl', lines=True),
        start_step=parent['step'], config=config, through_step=50000, contract_id=child['id'],
        probe=probe, prompts=prompts,
        generation_hashes=counts['segments'][-1]['generation_sha256_by_step'])
    if paths != sorted((main/'ledgers').glob('*.json')):
        raise ValueError('Final ledger membership changed during audit')
    categories = Counter()
    for group in probe['groups']:
        categories[group['category']] += counts['source_tokens'][group['source']]
    reader.snapshot.unchanged()
    return dict(status='passed_completed50k_metadata_and_ledger_audit',
        source_run=str(main), through_step=50000, counts=counts, category_tokens=dict(categories),
        trace=dict(parent=prefix['trace'], child=trace), evidence=reader.snapshot.bindings,
        final_metadata_verified=True, ledger_audit_verified=True,
        parent_payload_read=False, source_exit_verified=False, full_payload_sha_verified=False,
        actual8gpu_verified=False, retirement_authorized=False, goal_complete=False,
        code=code)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main-run', required=True)
    args = parser.parse_args()
    print(json.dumps(audit_completed(args.main_run), ensure_ascii=False, indent=2))
