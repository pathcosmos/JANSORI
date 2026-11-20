"""CPU-only final reservation invariants; these are not actual GPU restore tests."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from common.data_contracts import sha256_file
from scripts import completed_training_storage as m


@pytest.fixture
def source(tmp_path, monkeypatch):
    main = tmp_path / 'main'
    main.mkdir()
    transition = tmp_path / 'transition'
    transition.mkdir()
    auth = transition / 'authorization.json'
    approval = dict(authorized=True, scope=m.SCOPE, target_step=57000,
                    restore_deferred_budget_before_final_work=True,
                    deferred_pool_names=list(m.DEFERRED))
    m.atomic_json(auth, approval)
    pools = {name: dict(paths=[str(tmp_path / name)], budget_bytes=gib*m.GIB)
             for name, gib in zip(m.DEFERRED, (8, 1, 1, 100))}
    deferred = {name: dict(paths=row['paths'], budget_bytes=m.METADATA_ALLOWANCE)
                for name, row in pools.items()}
    deferred['transition'] = dict(paths=[str(transition)], budget_bytes=m.GIB)
    c = dict(purpose='main_training', target_steps=57000, main_run=str(main),
             code={}, evidence={str(auth): sha256_file(auth)},
             tiered_retention=dict(nfs_keep=3, local_keep=15),
             storage_phase_revision=dict(scope=m.SCOPE, authorization=str(auth),
                 restore_before_final_stage=True, unused_pool_metadata_cap_bytes=m.METADATA_ALLOWANCE,
                 deferred_pools=pools),
             shared_storage=dict(reserve_bytes=200*m.GIB, temporary_budget_bytes=1024*m.GIB,
                                 nfs_pools=deferred))
    m.atomic_json(main/'contract.json', c)
    monkeypatch.setattr(m, 'enforce_shared_space', lambda *a: {'fixture_capacity_only': True})
    return main, transition/'final_storage', c, auth


def test_only_four_budgets_change_source_bytes_and_future_outputs_preserved(source):
    main, root, c, _ = source
    before = (main/'contract.json').read_bytes()
    plan = m.prepare(main, root)
    projected = deepcopy(plan['projected_contract'])
    for name in m.DEFERRED:
        projected['shared_storage']['nfs_pools'][name] = c['shared_storage']['nfs_pools'][name]
    assert projected == c
    assert (main/'contract.json').read_bytes() == before
    assert m.load(root)[1] == plan['projected_contract']
    # Later consumer output is allowed; never rerun preparation or change source.
    output = Path(c['storage_phase_revision']['deferred_pools']['final_native']['paths'][0])
    output.mkdir()
    (output/'evidence.txt').write_text('preserve')
    assert m.load(root)[1] == plan['projected_contract']
    assert (output/'evidence.txt').read_text() == 'preserve'
    with pytest.raises(ValueError, match='Preserve'):
        m.prepare(main, root)


@pytest.mark.parametrize('fault', ['source', 'authorization', 'projection', 'code', 'retirement'])
def test_stale_or_tampered_preparation_is_rejected(source, fault):
    main, root, _, auth = source
    m.prepare(main, root)
    if fault == 'source':
        c = m.read(main/'contract.json'); c['target_steps'] = 35000
        m.atomic_json(main/'contract.json', c)
    elif fault == 'authorization':
        a = m.read(auth); a['authorized'] = False; m.atomic_json(auth, a)
    else:
        plan = m.read(root/'plan.json')
        if fault == 'projection':
            plan['projected_contract']['shared_storage']['reserve_bytes'] = 0
        elif fault == 'code':
            plan['code'][m.OWN] = '0'*64
        else:
            plan['parent_retirement_authorized'] = True
        m.atomic_json(root/'plan.json', plan)
        m.atomic_json(root/'prepared.json', dict(plan_sha256=sha256_file(root/'plan.json')))
    with pytest.raises(ValueError):
        m.load(root)


def test_insufficient_capacity_does_not_publish_or_modify_source(source, monkeypatch):
    main, root, _, _ = source
    before = (main/'contract.json').read_bytes()
    def fail(*a):
        raise RuntimeError('space')
    monkeypatch.setattr(m, 'enforce_shared_space', fail)
    with pytest.raises(RuntimeError, match='space'):
        m.prepare(main, root)
    assert not root.exists() and (main/'contract.json').read_bytes() == before


def test_live_exact_run_process_blocks_final_admission_without_signalling(source):
    main, root, _, _ = source
    m.prepare(main, root)
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)',
                              '--run-dir', str(main)])
    try:
        assert child.pid in [p['pid'] for p in m.active_source_processes(main)]
        with pytest.raises(RuntimeError, match='still active'):
            m.admit_completed(root)
        assert child.poll() is None
    finally:
        child.terminate(); child.wait(timeout=5)


def test_done_marker_alone_does_not_admit_final_work(source):
    main, root, _, _ = source
    m.prepare(main, root)
    m.atomic_json(main/'.done', {})
    with pytest.raises((KeyError, FileNotFoundError, ValueError)):
        m.admit_completed(root)


def test_capacity_rechecked_after_audit_and_source_reactivation_rejected(source, monkeypatch):
    main, root, _, _ = source
    m.prepare(main, root)
    monkeypatch.setattr('scripts.evaluate_restored_native.completed_main',
                        lambda _: (None, None, None, {'fixture_only': True}))
    calls = iter([[], [{'pid': 123}]])
    monkeypatch.setattr(m, 'active_source_processes', lambda _: next(calls))
    with pytest.raises(RuntimeError, match='became active'):
        m.admit_completed(root)


def test_completion_audit_failure_propagates_without_output(source, monkeypatch):
    main, root, _, _ = source
    m.prepare(main, root)
    before = sorted(p.name for p in root.iterdir())
    def fail(_):
        raise ValueError('missing rank or damaged checkpoint')
    monkeypatch.setattr('scripts.evaluate_restored_native.completed_main', fail)
    with pytest.raises(ValueError, match='missing rank'):
        m.admit_completed(root)
    assert sorted(p.name for p in root.iterdir()) == before


def test_explicit_reference_rejects_other_source_and_changed_plan(source):
    main, root, _, _ = source
    m.prepare(main, root)
    bound = m.bind(main, root)
    assert m.view(main, bound['reference']) == m.load(root)[1]
    with pytest.raises(ValueError, match='another training'):
        m.view(main.parent/'other', bound['reference'])
    with pytest.raises(ValueError, match='Explicit'):
        m.view(main, {})
    with pytest.raises(ValueError, match='reference changed'):
        m.view(main, dict(bound['reference'], plan_sha256='0'*64))


def test_native_contract_binds_original_source_and_separate_budget(source, monkeypatch):
    from scripts import evaluate_restored_native as native
    main, root, c, auth = source
    ref = main.parent/'reference'; ref.mkdir()
    tok = main.parent/'tokenizer.json'; tok.write_text('{}')
    m.atomic_json(main/'main.json', dict(model='3b', max_steps=57000))
    c.update(configurations={'main.json': sha256_file(main/'main.json')},
             tokenizer=str(tok), framework={}, retention_authorization=str(auth),
             retention_revision=dict(authorization=str(auth)))
    m.atomic_json(main/'contract.json', c)
    result = dict(contract={'tokenizer_sha256': sha256_file(tok)},
                  languages={k: dict(valid_tokens=40) for k in ['ko', 'en']})
    m.atomic_json(ref/'result.json', result)
    rc = dict(code={}, inputs={}, evaluation={}, samples='fixture', holdout_manifest='fixture')
    report = dict(result='result.json', result_sha256=sha256_file(ref/'result.json'))
    for name, value in [('contract.json', rc), ('report.json', report), ('.done', {})]:
        m.atomic_json(ref/name, value)
    monkeypatch.setattr('scripts.supervise_extended_training.require_authorization', lambda _: None)
    monkeypatch.setattr('train.extended_handoff.validate_configuration', lambda *a: None)
    monkeypatch.setattr('train.extended_handoff.load_handoff', lambda *a: None)
    monkeypatch.setattr(native, 'certified', lambda *a: (report, rc))
    m.prepare(main, root)
    digest = sha256_file(main/'contract.json')
    contract = native.make_contract(main, ref, storage_control=root)
    assert contract['late_start_step'] == 57000
    assert contract['inputs'][str(main/'contract.json')] == digest
    assert contract['inputs'][str(root/'plan.json')] == sha256_file(root/'plan.json')
    output = Path(c['storage_phase_revision']['deferred_pools']['final_native']['paths'][0])
    source_main, budget = native.reserved_pool(contract, output)
    assert source_main == main
    assert budget['shared_storage']['nfs_pools']['final_native']['budget_bytes'] == 8*m.GIB
    assert sha256_file(main/'contract.json') == digest and not output.exists()
    assert m.read(main/'contract.json')['shared_storage']['nfs_pools']['final_native']['budget_bytes'] == m.METADATA_ALLOWANCE
    with pytest.raises(ValueError, match='exact8GiB'):
        native.reserved_pool(contract, main.parent/'wrong-output')
    with pytest.raises(ValueError, match='exactly one'):
        native.make_contract(main, ref, runtime_control='other', storage_control=root)


@pytest.mark.parametrize('worker', [False, True])
def test_native_controller_and_worker_reject_live_source_before_output(source, monkeypatch, worker):
    from argparse import Namespace
    from scripts import evaluate_restored_native as native
    main, root, c, _ = source
    m.prepare(main, root)
    output = main.parent/'native-gate-test'
    contract = dict(code={}, inputs={}, main_run=str(main), final_storage=m.bind(main, root)['reference'])
    if worker:
        output.mkdir(); m.atomic_json(output/'contract.json', contract)
    monkeypatch.setattr(native, 'make_contract', lambda *a, **k: contract)
    monkeypatch.setattr(native, 'resolve', lambda p: main.parent if p == 'runs' else Path(p))
    monkeypatch.setattr(native, 'admission', lambda *a: pytest.fail('must reject before resource setup'))
    child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)', '--run-dir', str(main)])
    try:
        with pytest.raises(RuntimeError, match='still active'):
            native.main(Namespace(worker=worker, run_dir=str(output), main_run=str(main),
                                  reference_run='unused', prepare=True, storage_control=str(root)))
        assert child.poll() is None
        if worker:
            assert sorted(p.name for p in output.iterdir()) == ['contract.json']
        else:
            assert not output.exists()
    finally:
        child.terminate(); child.wait(timeout=5)
