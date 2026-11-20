"""Fresh byte-bound metadata inspection for a future final-only50k restore.

Read-only: never opens an ancestor parent_checkpoint subtree or any .distcp
payload. An identity result is not an all-rank ledger audit, a tensor check,
process-exit proof, or permission to start a diagnostic/copy/retirement.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve, sha256_file
from scripts.audit_checkpoint_retention import Snapshot, ChangedDuringAudit
from scripts.tiered_checkpoint_retention import exact_path
from train.final_resume_probe import stat_record
from scripts.budget_completed_identity import reconstruct_identity, verify_completed_metadata

OWN = 'scripts/inspect_budget_completed_identity.py'


class MetadataReader:
    def __init__(self):
        self.snapshot = Snapshot()
        self.denied = []

    def path(self, name):
        path = exact_path(resolve(name))
        if path.suffix == '.distcp' or any(path == d or path.is_relative_to(d) for d in self.denied):
            raise ValueError('Ancestor/payload access forbidden in metadata-only reader')
        return path

    def read(self, name):
        path = self.path(name)
        if str(path) in self.snapshot.files:
            raise ValueError('Repeated metadata read would replace the first snapshot')
        return self.snapshot.read(path)

    def pin(self, name, expected):
        path = self.path(name)
        saved = self.snapshot.files.get(str(path))
        if saved is None:
            before = stat_record(path)
            digest = sha256_file(path)
            if stat_record(path) != before:
                raise ChangedDuringAudit('Bound metadata changed while hashing')
            self.snapshot.files[str(path)] = (before, digest)
        else:
            digest = saved[1]
        if digest != expected:
            raise ValueError('Bound metadata/code SHA differs: ' + str(path))

    def digest(self, name):
        return self.snapshot.files[str(self.path(name))][1]


def inspect(main, *, final=False):
    main = exact_path(resolve(main))
    reader = MetadataReader()
    contract = reader.read(main/'contract.json')
    if resolve(contract['main_run']) != main:
        raise ValueError('Main contract namespace differs')
    reader.denied = [exact_path(resolve(d['run']))/'parent_checkpoint'
                     for d in contract['retention_branches']]
    config = reader.read(main/'main.json')
    receipt = reader.read(main/'parent_handoff.json')
    proof = reader.read(main/'parent_certificate.json')
    actual = reader.read(main/'training/training_contract.json')
    if contract['configurations'] != {'main.json': reader.digest(main/'main.json')}:
        raise ValueError('Production config byte binding differs')
    if contract['evidence'].get(str(main/'parent_handoff.json')) != reader.digest(main/'parent_handoff.json'):
        raise ValueError('Exact parent handoff byte binding missing')
    candidate = reconstruct_identity(contract, config, receipt, proof, actual,
        parent_certificate_sha256=reader.digest(main/'parent_certificate.json'))
    # Keep both maps: a colliding evidence key must not hide a code mismatch.
    for bindings in (contract['code'], contract['evidence']):
        for name, digest in bindings.items():
            reader.pin(name, digest)
    result = dict(status='verified_byte_bound50k_identity_only', main_run=str(main),
        training_contract_id=candidate['id'], target_steps=50000,
        parent_step=receipt['parent_metadata']['step'], parent_payload_read=False,
        final_metadata_verified=False, full_parent_scope_verified=False,
        ledger_audit_verified=False, source_exit_verified=False,
        full_payload_sha_verified=False, actual8gpu_verified=False,
        retirement_authorized=False, goal_complete=False)
    if final:
        checkpoint = main/'training/ckpt_0050000'
        meta = reader.read(checkpoint/'meta.json')
        certificate = reader.read(main/'checkpoint_certificates/ckpt_0050000.json')
        report = reader.read(main/'report.json')
        marker = reader.read(main/'.done')
        result['completion'] = verify_completed_metadata(contract, config, candidate, meta,
            certificate, report, marker, report_sha256=reader.digest(main/'report.json'),
            contract_sha256=reader.digest(main/'contract.json'))
        meta_row = next(r for r in certificate['files'] if r['path'] == 'meta.json')
        if (reader.digest(checkpoint/'meta.json') != meta_row['sha256'] or
                reader.snapshot.files[str(checkpoint/'meta.json')][0]['st_size'] != meta_row['bytes']):
            raise ValueError('Final metadata byte SHA/size differs from certificate')
        result['final_metadata_verified'] = True
    for name in [OWN, 'scripts/budget_completed_identity.py', 'scripts/budget_final_lineage.py',
                 'scripts/audit_checkpoint_retention.py', 'scripts/tiered_checkpoint_retention.py',
                 'train/budget_horizon_contract.py', 'train/budget_horizon_handoff.py']:
        reader.pin(name, sha256_file(resolve(name)))
    reader.snapshot.unchanged()
    result['metadata_inputs'] = {p: row[1] for p, row in reader.snapshot.files.items()}
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main-run', required=True)
    parser.add_argument('--final', action='store_true')
    args = parser.parse_args()
    print(json.dumps(inspect(args.main_run, final=args.final), ensure_ascii=False, indent=2))
