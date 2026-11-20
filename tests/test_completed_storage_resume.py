"""Separate-storage resume protocol; final audit/GPU execution are synthetic."""
from copy import deepcopy
from pathlib import Path

import pytest

from common.data_contracts import sha256_file, identity
from scripts import completed_training_storage as storage
from scripts.prepare_restored_final_resume import prepare
from scripts.run_restored_final_resume import make_contract
from train.restored_final_resume_probe import check_spec, file_stats
import test_restored_final_resume_admission as fixture_module


@pytest.fixture
def case(monkeypatch):
    f = fixture_module.FinalHorizonAdmissionTests()
    f.setUp()
    try:
        transition = f.base/'transition'; transition.mkdir()
        auth = transition/'authorization.json'
        f.write(auth, dict(authorized=True, scope=storage.SCOPE, target_step=57000,
                          restore_deferred_budget_before_final_work=True,
                          deferred_pool_names=list(storage.DEFERRED)))
        pools = dict(final_native=dict(paths=[str(f.native)], budget_bytes=8*2**30),
                     final_resume=dict(paths=[str(f.output)], budget_bytes=2**30),
                     final_retention=dict(paths=[str(f.base/'retention')], budget_bytes=2**30),
                     hf_and_serving=dict(paths=[str(f.base/'export')], budget_bytes=100*2**30))
        f.mc.update(main_run=str(f.main), target_steps=57000,
                    storage_phase_revision=dict(scope=storage.SCOPE, authorization=str(auth),
                        restore_before_final_stage=True, unused_pool_metadata_cap_bytes=storage.METADATA_ALLOWANCE,
                        deferred_pools=deepcopy(pools)))
        f.mc['evidence'][str(auth)] = sha256_file(auth)
        f.mc['tiered_retention'].update(nfs_keep=3, local_keep=15)
        f.mc['shared_storage'].update(reserve_bytes=200*2**30, temporary_budget_bytes=1024*2**30,
            nfs_pools={**{k: dict(paths=v['paths'], budget_bytes=storage.METADATA_ALLOWANCE)
                         for k, v in pools.items()},
                       'transition': dict(paths=[str(transition)], budget_bytes=2**30)})
        f.write(f.main/'contract.json', f.mc)
        monkeypatch.setattr(storage, 'enforce_shared_space', lambda *a: {'fixture_only': True})
        f.control = transition/'storage'
        storage.prepare(f.main, f.control)
        f.reference = storage.bind(f.main, f.control)['reference']
        f.nc['final_storage'] = f.reference
        f.write(f.native/'contract.json', f.nc)
        monkeypatch.setattr(storage, 'admit_reference', lambda *a: {'synthetic_final_audit_only': True})
        f.plan = lambda: prepare(f.main, f.output, native_run=f.native, storage_control=f.control)
        yield f
    finally:
        f.doCleanups()


def test_plan_controller_and_worker_spec_share_exact_storage_and_original_identity(case):
    f = case
    before = file_stats(f.main)
    spec = f.spec()
    plan, config, parent = check_spec(spec)
    controller = make_contract(f.output)
    assert plan['final_storage'] == controller['final_storage'] == f.reference
    assert parent == f.parent and config['max_steps'] == 57000
    assert plan['inputs'][str(f.control/'plan.json')] == sha256_file(f.control/'plan.json')
    assert storage.read(f.main/'contract.json')['shared_storage']['nfs_pools']['final_resume']['budget_bytes'] == storage.METADATA_ALLOWANCE
    after = file_stats(f.main)
    assert all(after[k] == v for k, v in before.items())
    assert not Path(plan['scratch']).exists()


def test_native_reference_mismatch_refused_before_resume_output(case):
    f = case
    f.nc['final_storage'] = dict(f.reference, plan_sha256='0'*64)
    f.write(f.native/'contract.json', f.nc)
    with pytest.raises(ValueError, match='separate storage binding'):
        f.plan()
    assert not f.output.exists()


def test_incomplete_source_blocks_preparation_before_output(case, monkeypatch):
    f = case
    def fail(*a):
        raise RuntimeError('Training source still active')
    monkeypatch.setattr(storage, 'admit_reference', fail)
    with pytest.raises(RuntimeError, match='still active'):
        f.plan()
    assert not f.output.exists()


def test_worker_rejects_missing_storage_proof_even_with_rebuilt_plan_identity(case):
    spec = case.spec()
    spec['plan']['inputs'].pop(str(case.control/'plan.json'))
    spec['plan_id'] = identity(spec['plan'])
    with pytest.raises(ValueError, match='separate storage code/input'):
        check_spec(spec)


def test_runtime_and_storage_routes_cannot_be_mixed(case):
    f = case
    with pytest.raises(ValueError, match='exactly one'):
        prepare(f.main, f.output, native_run=f.native, runtime_control='other', storage_control=f.control)
    assert not f.output.exists()
