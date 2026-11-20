"""Metadata-only scope and space tests; no production payload or GPU actions."""
from copy import deepcopy
import json
from pathlib import Path
import pytest

from common.data_contracts import identity, sha256_file
from scripts.budget_final_lineage import intervals
from scripts.budget_final_parent_scope import review_scope, inspect_scope, serial_capacity, GIB


def documents(tmp_path):
    owners = [tmp_path/n for n in ('original', 'short', 'restored', 'budget')]
    contracts = []
    for index, horizon in enumerate((57000, 35000, 57000, 50000)):
        c = dict(world_size=8, config={'max_steps': horizon}, fixture_identity=index)
        c['id'] = identity(c)
        contracts.append(c)
    main = dict(main_run=str(owners[-1]), target_steps=50000,
        budget_execution={'parent_step': 16000}, retention_branches=[
            dict(run=str(owner), first_step=a, last_step=b)
            for owner, (a, b) in zip(owners, intervals(16000, checkpoints=True))])
    handoffs, certificates = [], []
    for index, step in enumerate((8000, 10000, 16000)):
        files = [dict(path=n, bytes=1, sha256='a'*64) for n in
            ['.complete', '.metadata', 'meta.json', 'state.distcp',
             *[f'rng_rank{rank}.pt' for rank in range(8)]]]
        cert = dict(status='passed_main_checkpoint_storage', step=step,
            training_contract_id=contracts[index]['id'], files=files, bytes=len(files),
            checkpoint=str(owners[index]/'training'/f'ckpt_{step:07d}'))
        meta = dict(step=step, world_size=8, tokens_per_step=2097152,
            training_contract=deepcopy(contracts[index]))
        receipt = dict(parent_checkpoint=str(owners[index+1]/'parent_checkpoint'/f'ckpt_{step:07d}'),
            parent_metadata=meta, candidate_contract_id=contracts[index+1]['id'],
            parent_stats={r['path']:dict(st_dev=1, st_ino=i+1, st_size=r['bytes'],
                st_mtime_ns=1, st_ctime_ns=1) for i, r in enumerate(files)})
        handoffs.append(receipt)
        certificates.append(cert)
    return main, contracts, handoffs, certificates


def test_three_parent_scope_without_reading_payload(tmp_path):
    result = review_scope(*documents(tmp_path))
    assert [e['step'] for e in result['entries']] == [8000, 10000, 16000]
    assert result['bytes'] == 36
    assert not any(result[k] for k in ('full_payload_sha_verified', 'actual8gpu_verified',
                                     'execution_authorized', 'goal_complete'))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('fault', ['old_two_parents', 'old_three_owners', 'wrong_boundary',
    'wrong_target', 'wrong_child', 'wrong_parent', 'changed_identity', 'wrong_horizon',
    'original_as_protected', 'protected_as_original', 'outside_owner', 'missing_rng',
    'duplicate_file', 'unsafe_file', 'bad_sha', 'wrong_stat', 'extra_stat',
    'boolean_step', 'boolean_size', 'boolean_stat', 'bad_certificate', 'rank_omission'])
def test_rejects_old_or_inconsistent_scope(tmp_path, fault):
    main, contracts, handoffs, certificates = documents(tmp_path)
    receipt, cert = handoffs[-1], certificates[-1]
    if fault == 'old_two_parents': handoffs.pop(); certificates.pop()
    elif fault == 'old_three_owners': main['retention_branches'].pop(0)
    elif fault == 'wrong_boundary': main['budget_execution']['parent_step'] = 16500
    elif fault == 'wrong_target': main['target_steps'] = 57000
    elif fault == 'wrong_child': receipt['candidate_contract_id'] = contracts[0]['id']
    elif fault == 'wrong_parent': receipt['parent_metadata']['training_contract'] = contracts[1]
    elif fault == 'changed_identity': contracts[0]['fixture_identity'] = 99
    elif fault == 'wrong_horizon':
        contracts[-1]['config']['max_steps'] = 57000
        contracts[-1]['id'] = identity({k:v for k,v in contracts[-1].items() if k != 'id'})
    elif fault == 'original_as_protected': receipt['parent_checkpoint'] = cert['checkpoint']
    elif fault == 'protected_as_original': cert['checkpoint'] = receipt['parent_checkpoint']
    elif fault == 'outside_owner': main['retention_branches'][0]['run'] = str(tmp_path.parent/'outside')
    elif fault == 'missing_rng': cert['files'].pop(); cert['bytes'] -= 1
    elif fault == 'duplicate_file': cert['files'].append(deepcopy(cert['files'][0])); cert['bytes'] += 1
    elif fault == 'unsafe_file': cert['files'][0]['path'] = '../outside'
    elif fault == 'bad_sha': cert['files'][0]['sha256'] = 'invalid'
    elif fault == 'wrong_stat': receipt['parent_stats']['meta.json']['st_size'] += 1
    elif fault == 'extra_stat': receipt['parent_stats']['outside'] = receipt['parent_stats']['meta.json']
    elif fault == 'boolean_step': receipt['parent_metadata']['step'] = True
    elif fault == 'boolean_size': cert['bytes'] = True
    elif fault == 'boolean_stat': receipt['parent_stats']['meta.json']['st_size'] = True
    elif fault == 'bad_certificate': cert['status'] = 'copied_not_verified'
    elif fault == 'rank_omission': receipt['parent_metadata']['world_size'] = 7
    with pytest.raises(ValueError): review_scope(main, contracts, handoffs, certificates)


def test_symlink_owner_rejected(tmp_path):
    docs = documents(tmp_path)
    actual = tmp_path/'different'; actual.mkdir()
    (tmp_path/'original').symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError): review_scope(*docs)


def test_naive_peak_fails_and_measured_serial_peak_can_pass():
    prior = serial_capacity(temporary_used_bytes=949*GIB, local_free_bytes=500*GIB,
                            parent_bytes=108*GIB)
    assert not prior['admissible']
    assert prior['violations'] == ['temporary_pool_1024GiB_exceeded']
    # Hypothetical measured final outputs below their reserved peak; no deletion.
    serial = serial_capacity(temporary_used_bytes=900*GIB, local_free_bytes=500*GIB,
                             parent_bytes=108*GIB, other_local_future_bytes=50*GIB)
    assert serial['admissible'] and serial['deletion_credit_bytes'] == 0
    assert not serial['execution_authorized'] and serial['requires_fresh_measurement']


def test_resident_partial_archive_not_counted_twice():
    before = serial_capacity(temporary_used_bytes=900*GIB, local_free_bytes=500*GIB,
                             parent_bytes=100*GIB)
    partial = serial_capacity(temporary_used_bytes=925*GIB, local_free_bytes=475*GIB,
        parent_bytes=100*GIB, resident_archive_bytes=25*GIB)
    assert before['temporary_peak_bytes'] == partial['temporary_peak_bytes']
    assert before['local_free_after_future_bytes'] == partial['local_free_after_future_bytes']


def test_retained_final_outputs_and_future_history_still_charged():
    result = serial_capacity(temporary_used_bytes=900*GIB, local_free_bytes=350*GIB,
        parent_bytes=100*GIB, other_local_future_bytes=50*GIB)
    assert result['violations'] == ['local_safety_reserve_200GiB_violated']
    with_probe = serial_capacity(temporary_used_bytes=900*GIB, local_free_bytes=500*GIB,
        parent_bytes=100*GIB, diagnostic_increment_bytes=36*GIB)
    assert 'temporary_pool_1024GiB_exceeded' in with_probe['violations']


@pytest.mark.parametrize('changes', [dict(parent_bytes=0), dict(parent_bytes=True),
    dict(local_free_bytes=-1), dict(diagnostic_increment_bytes=1.5),
    dict(resident_archive_bytes=111*GIB),
    dict(temporary_used_bytes=GIB, resident_archive_bytes=2*GIB)])
def test_invalid_capacity_inputs_rejected(changes):
    args = dict(temporary_used_bytes=900*GIB, local_free_bytes=500*GIB, parent_bytes=100*GIB)
    args.update(changes)
    with pytest.raises(ValueError): serial_capacity(**args)


def write_metadata_fixture(tmp_path):
    main, contracts, handoffs, certificates = documents(tmp_path)
    main['purpose'] = 'main_training'
    for i, definition in enumerate(main['retention_branches']):
        owner = Path(definition['run']); (owner/'training').mkdir(parents=True)
        (owner/'main.json').write_text(json.dumps(contracts[i]['config']))
        (owner/'training/training_contract.json').write_text(json.dumps(contracts[i]))
        if i < 3:
            value = {'configurations': {'main.json': sha256_file(owner/'main.json')}}
            (owner/'contract.json').write_text(json.dumps(value))
            definition['contract_sha256'] = sha256_file(owner/'contract.json')
        else:
            main['configurations'] = {'main.json': sha256_file(owner/'main.json')}
            (owner/'contract.json').write_text(json.dumps(main))
        if i:
            (owner/'parent_handoff.json').write_text(json.dumps(handoffs[i-1]))
            (owner/'parent_certificate.json').write_text(json.dumps(certificates[i-1]))
    return Path(main['main_run'])


def test_disk_metadata_inspection_never_opens_or_creates_payload(tmp_path):
    main = write_metadata_fixture(tmp_path)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result = inspect_scope(main)
    assert len(result['metadata_inputs']) == 18
    assert not result['current_payload_presence_checked']
    assert not result['execution_authorized']
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert not list(tmp_path.rglob('*.distcp'))


@pytest.mark.parametrize('fault', ['owner_pin', 'config_pin', 'preparation'])
def test_disk_inspection_rejects_changed_or_unactivated_inputs(tmp_path, fault):
    main = write_metadata_fixture(tmp_path)
    if fault == 'owner_pin':
        with (tmp_path/'original/contract.json').open('a') as f: f.write(' ')
    elif fault == 'config_pin':
        with (tmp_path/'original/main.json').open('a') as f: f.write(' ')
    else:
        path = main/'contract.json'; value = json.loads(path.read_text())
        value['purpose'] = 'budget_preparation_not_executable'
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError): inspect_scope(main)


def test_main_contract_change_during_inspection_cannot_replace_first_snapshot(tmp_path, monkeypatch):
    from scripts.audit_checkpoint_retention import Snapshot, ChangedDuringAudit
    main = write_metadata_fixture(tmp_path)
    original = Snapshot.read
    def read_then_mutate(self, path, **kwargs):
        value = original(self, path, **kwargs)
        if path == tmp_path/'original/contract.json':
            with (main/'contract.json').open('a') as f: f.write(' ')
        return value
    monkeypatch.setattr(Snapshot, 'read', read_then_mutate)
    with pytest.raises(ChangedDuringAudit): inspect_scope(main)
