"""Read-only scope and capacity checks for final50k three-parent retirement.

These helpers do not authorize retirement or prove tensor restoration. A future
completed-source controller must bind fresh metadata, all8 NFS-only restoration,
copy acknowledgements, process absence and the exact reviewed execution plan.
The currently frozen training/transition/final consumers do not import this file.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.data_contracts import identity, resolve, sha256_file
from scripts.audit_checkpoint_retention import Snapshot
from scripts.budget_final_lineage import contract_intervals
from scripts.tiered_checkpoint_retention import exact_path, records

GIB = 2**30


def _integer(value, name, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('Invalid nonnegative integer: ' + name)
    return value


def review_scope(contract, training_contracts, handoffs, certificates):
    """Validate exactly three protected parents from four original identities.

    Inputs are already parsed metadata, ordered by retention owner. This does
    not read checkpoint payloads or claim current files match their certificates.
    Source certificates name the original training path; protected payloads live
    in the following owner's parent_checkpoint directory. Do not conflate them.
    """
    intervals = contract_intervals(contract)
    owners = [exact_path(resolve(d['run'])) for d in contract['retention_branches']]
    if any(p.parent != owners[-1].parent for p in owners):
        raise ValueError('Four sibling run owners required')
    if len(training_contracts) != 4 or len(handoffs) != 3 or len(certificates) != 3:
        raise ValueError('Exactly four identities and three protected parents required')
    ids = []
    for value, horizon in zip(training_contracts, (57000, 35000, 57000, 50000)):
        digest = identity({k: v for k, v in value.items() if k != 'id'})
        if (value.get('id') != digest or value.get('world_size') != 8 or
                value.get('config', {}).get('max_steps') != horizon):
            raise ValueError('Original training identity/world/horizon differs')
        ids.append(digest)
    if len(set(ids)) != 4:
        raise ValueError('Four distinct original training identities required')
    entries = []
    for index, (receipt, proof) in enumerate(zip(handoffs, certificates)):
        step = intervals[index][1]
        owner, previous = owners[index + 1], owners[index]
        name = f'ckpt_{step:07d}'
        protected = owner/'parent_checkpoint'/name
        origin = previous/'training'/name
        if (exact_path(resolve(receipt['parent_checkpoint'])) != protected or
                exact_path(resolve(proof['checkpoint'])) != origin):
            raise ValueError('Protected parent or original certificate path differs')
        metadata = receipt['parent_metadata']
        if (type(metadata.get('step')) is not int or metadata['step'] != step or
                metadata.get('world_size') != 8 or metadata.get('tokens_per_step') != 2097152 or
                metadata.get('training_contract') != training_contracts[index] or
                receipt.get('candidate_contract_id') != ids[index + 1]):
            raise ValueError('Protected metadata/child identity differs')
        if (proof.get('status') != 'passed_main_checkpoint_storage' or
                type(proof.get('step')) is not int or proof['step'] != step or
                proof.get('training_contract_id') != ids[index]):
            raise ValueError('Protected certificate identity differs')
        _integer(proof['bytes'], 'certificate bytes', minimum=1)
        files = records(proof)
        stats = receipt['parent_stats']
        if set(files) != set(stats):
            raise ValueError('Protected stat inventory coverage differs')
        for name, row in stats.items():
            for field in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'):
                _integer(row[field], field)
            if row['st_size'] != files[name]['bytes']:
                raise ValueError('Protected stat inventory size differs')
        entries.append(dict(owner=str(owner), previous_owner=str(previous), step=step,
            protected_checkpoint=str(protected), original_checkpoint=str(origin),
            parent_contract_id=ids[index], child_contract_id=ids[index + 1],
            certificate_id=identity(proof), metadata_id=identity(metadata),
            files=sorted(files.values(), key=lambda row: row['path']), bytes=proof['bytes']))
    return dict(status='reviewed_four_generation_parent_metadata_scope', entries=entries,
        parent_count=3, bytes=sum(e['bytes'] for e in entries), target_steps=50000,
        full_payload_sha_verified=False, actual8gpu_verified=False,
        execution_authorized=False, goal_complete=False)


def inspect_scope(main):
    """Read immutable metadata only; never repair/create/copy/remove a payload."""
    main = exact_path(resolve(main))
    snapshot = Snapshot()
    contract = snapshot.read(main/'contract.json')
    if contract.get('purpose') != 'main_training' or resolve(contract['main_run']) != main:
        raise ValueError('An activated main contract is required; preparation is insufficient')
    contract_intervals(contract)
    contracts, handoffs, certificates = [], [], []
    for index, definition in enumerate(contract['retention_branches']):
        owner = exact_path(resolve(definition['run']))
        # Do not overwrite the first main-contract stat snapshot with a later
        # read: that would hide a concurrent change during this inspection.
        owner_contract = contract if owner == main else snapshot.read(owner/'contract.json')
        snapshot.read(owner/'main.json')
        if (index < 3 and snapshot.files[str(owner/'contract.json')][1] !=
                definition['contract_sha256']):
            raise ValueError('Historical owner contract SHA differs')
        if owner_contract['configurations'] != {'main.json': snapshot.files[str(owner/'main.json')][1]}:
            raise ValueError('Owner configuration SHA differs')
        contracts.append(snapshot.read(owner/'training/training_contract.json'))
        if index:
            handoffs.append(snapshot.read(owner/'parent_handoff.json'))
            certificates.append(snapshot.read(owner/'parent_certificate.json'))
    result = review_scope(contract, contracts, handoffs, certificates)
    snapshot.unchanged()
    result.update(main_run=str(main), metadata_inputs={p: row[1] for p, row in snapshot.files.items()},
        code={name: sha256_file(resolve(name)) for name in
            ['scripts/budget_final_parent_scope.py', 'scripts/budget_final_lineage.py',
             'scripts/audit_checkpoint_retention.py', 'scripts/tiered_checkpoint_retention.py']},
        current_payload_presence_checked=False)
    return result


def serial_capacity(*, temporary_used_bytes, local_free_bytes, parent_bytes,
                    resident_archive_bytes=0, diagnostic_increment_bytes=0,
                    other_local_future_bytes=0):
    """Measure after final consumers stop; retain their actual resident outputs.

    temporary_used includes resident_archive and every retained diagnostic/env.
    other_local_future excludes the archive/diagnostic increments calculated
    here; it includes remaining history growth and other concurrent reservations.
    No deletion credit, file-cache credit, history reclassification or NFS credit.
    A passing projection is only a space check, never permission to copy/delete.
    """
    values = locals().copy()
    for name, value in values.items():
        _integer(value, name)
    if parent_bytes == 0 or resident_archive_bytes > temporary_used_bytes:
        raise ValueError('Missing parents or archive absent from measured temporary usage')
    archive_cap = (parent_bytes * 11 + 9) // 10
    if resident_archive_bytes > archive_cap:
        raise ValueError('Parent archive residue exceeds its bounded budget')
    remaining = archive_cap - resident_archive_bytes
    increment = remaining + diagnostic_increment_bytes
    peak = temporary_used_bytes + increment
    free_after = local_free_bytes - increment - other_local_future_bytes
    violations = []
    if peak > 1024*GIB:
        violations.append('temporary_pool_1024GiB_exceeded')
    if free_after < 200*GIB:
        violations.append('local_safety_reserve_200GiB_violated')
    return dict(status='capacity_projection_only', admissible=not violations,
        violations=violations, temporary_peak_bytes=peak,
        archive_cap_bytes=archive_cap, remaining_archive_bytes=remaining,
        local_free_after_future_bytes=free_after,
        safety_reserve_bytes=200*GIB, deletion_credit_bytes=0,
        execution_authorized=False, requires_fresh_measurement=True,
        nfs_capacity_checked=False, source_process_absence_checked=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main-run', required=True)
    args = parser.parse_args()
    print(json.dumps(inspect_scope(args.main_run), ensure_ascii=False, indent=2))
