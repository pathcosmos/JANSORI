"""NFS publication/lineage/locks; synthetic GPU and parent-plan acceptance."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
from copy import deepcopy

import pytest
from common.data_contracts import resolve,sha256_file,identity
from scripts.tiered_checkpoint_retention import atomic_json
from scripts import prepare_independent_runtime as m
from scripts import independent_observer_handoff as observer
from scripts import restore_final_storage as storage
from train import independent_runtime_contract as gate
from test_independent_checkpoint_handoff import case
from test_restore_final_storage import setup
from test_independent_observer_handoff import handoff


@pytest.fixture
def candidate(setup,monkeypatch):
    import torch
    setup['c'].contract.update(max_seconds=1000000,framework=dict(torch=str(torch.__version__),cuda=torch.version.cuda))
    e=handoff.__wrapped__(setup);c=e['c']
    observer.freeze(e['root']);storage.publish(e['root'])
    control=e['pool']/'runtime';control.mkdir();atomic_json(control/'parent_retirement_plan.json',{'fixture_parent_plan':True})
    diagnostic=e['pool']/'diagnostic';(diagnostic/'accepted_evidence').mkdir(parents=True)
    for name in ['manifest.json','verified.json']:atomic_json(diagnostic/'accepted_evidence'/name,{'synthetic_gpu_fixture':True})
    accepted=dict(status='verified_historical_independent8gpu_resume_evidence',parent_step=10500,
        parent_training_contract_id=c.candidate['id'],target_steps=57000,source_run=str(c.root))
    monkeypatch.setattr(m,'verify_acceptance',lambda *a:dict(accepted))
    monkeypatch.setattr(m,'tested_source',lambda *a:None)
    monkeypatch.setattr(gate,'verify_acceptance',lambda *a:dict(accepted))
    monkeypatch.setattr(m,'reviewed_plan',lambda view,snap:snap.read(view.control/'parent_retirement_plan.json'))
    # The tiny nested fixture namespaces do not reserve another real1TiB pool.
    # The production implementation still enforces the original shared guard.
    monkeypatch.setattr(m,'enforce_shared_space',lambda *a:{'synthetic_fixture_only':True})
    monkeypatch.setattr('scripts.supervise_extended_training.require_authorization',lambda c:None)
    monkeypatch.setenv('TMPDIR','/tmp')
    e.update(control=control,diagnostic=diagnostic,accepted=accepted)
    return e


def prepare(e,elapsed=123):
    return m.prepare(e['c'].root,e['control'],e['diagnostic'],e['root'],elapsed_before_seconds=elapsed)


def authorize(e,review):
    binding=review['binding'];path=e['control']/'authorization.json'
    atomic_json(path,dict(purpose='independent_main_runtime_authorization_v1',authorized=True,
        binding_id=identity(binding),main_run=binding['main_run']))
    return path


def test_review_then_exact_external_authorization_publication_without_launch(candidate):
    e=candidate;original=(e['c'].root/'main.json').read_bytes();review=prepare(e)
    assert not (e['control']/'runtime_contract.json').exists() and not (e['control']/'authorization.json').exists()
    with pytest.raises(FileNotFoundError):m.publish(e['control'],e['control']/'authorization.json')
    auth=authorize(e,review);report=m.publish(e['control'],auth)
    assert report['boundary_step']==10500 and report['target_steps']==57000 and not report['training_started']
    assert not (e['control']/'attempts').exists() and not (e['old']/'independent_v1').exists()
    assert (e['c'].root/'main.json').read_bytes()==original and e['c'].parent.exists()
    clock=json.loads((e['control']/'elapsed_clock.json').read_text())
    assert clock['started_unix']==review['prepared_unix']
    assert m.publish(e['control'],auth)==report


@pytest.mark.parametrize('phase',['before_runtime','after_runtime'])
def test_interrupted_publication_preserved_and_explicit_same_plan_resumes(candidate,phase):
    e=candidate;review=prepare(e);auth=authorize(e,review)
    def fault(p):
        if p==phase:raise InterruptedError('fixture interruption')
    with pytest.raises(InterruptedError):m.publish(e['control'],auth,fault=fault)
    assert (e['control']/'publication_intent.json').exists()
    assert (e['control']/'runtime_contract.json').exists()==(phase=='after_runtime')
    assert not (e['control']/'publication_report.json').exists()
    assert m.publish(e['control'],auth)['training_started'] is False


@pytest.mark.parametrize('elapsed',[-1,True,float('nan'),float('inf'),1000000])
def test_invalid_elapsed_budget_rejected_without_review(candidate,elapsed):
    e=candidate
    with pytest.raises(ValueError):prepare(e,elapsed)
    assert not (e['control']/'review.json').exists()


@pytest.mark.parametrize('fault',['no_restoration','no_gpu_proof','gpu_boundary','parent_missing','observer_changed','space'])
def test_missing_actual_gate_never_prepares_runtime(candidate,monkeypatch,fault):
    e=candidate
    if fault=='no_restoration':(e['root']/'.done').unlink()
    elif fault=='no_gpu_proof':monkeypatch.setattr(m,'verify_acceptance',lambda *a:(_ for _ in ()).throw(ValueError('missing actual8GPU')))
    elif fault=='gpu_boundary':e['accepted']['parent_step']=11000
    elif fault=='parent_missing':(e['control']/'parent_retirement_plan.json').unlink()
    elif fault=='observer_changed':(e['root']/'observer_handoff/cursor.json').write_text('{}')
    elif fault=='space':monkeypatch.setattr(m,'enforce_shared_space',lambda *a:(_ for _ in ()).throw(RuntimeError('space unavailable')))
    with pytest.raises((ValueError,FileNotFoundError,RuntimeError)):prepare(e)
    assert not (e['control']/'review.json').exists()


@pytest.mark.parametrize('fault',['auth','review','main','parent','clock','elapsed_clock','lock','temporary_path'])
def test_changed_scope_and_live_controller_lock_prevent_publication(candidate,monkeypatch,fault):
    e=candidate;review=prepare(e);auth=authorize(e,review);lock=None
    if fault=='auth':atomic_json(auth,{'authorized':False})
    elif fault=='review':(e['control']/'review.json').write_text('{}')
    elif fault=='main':p=e['c'].root/'main.json';p.write_text(p.read_text()+' ')
    elif fault=='parent':(e['control']/'parent_retirement_plan.json').write_text('{}')
    elif fault=='clock':monkeypatch.setattr(m.time,'time',lambda:review['prepared_unix']-1)
    elif fault=='elapsed_clock':atomic_json(e['control']/'elapsed_clock.json',{'unowned':True})
    elif fault=='lock':
        lock=(e['c'].root/'controller.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    elif fault=='temporary_path':monkeypatch.setenv('TMPDIR','/tmp/wrong-nested-worker-path')
    try:
        with pytest.raises((ValueError,BlockingIOError)):m.publish(e['control'],auth)
        assert not (e['control']/'publication_report.json').exists() and not (e['control']/'attempts').exists()
    finally:
        if lock:lock.close()


def test_captured_live_producer_is_not_stopped_or_adopted(candidate):
    e=candidate;review=prepare(e);p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
    try:
        from scripts.horizon_process_handoff import process_record
        row=process_record(p.pid);binding=deepcopy(review['binding'])
        binding['source_capture']['processes']['rank0']=row
        with pytest.raises(ValueError,match='Captured parent still present'):
            m.check(binding,e['control'])
        assert p.poll() is None and not (e['control']/'runtime_contract.json').exists()
    finally:p.terminate();p.wait(timeout=5)


@pytest.mark.parametrize('fault',['none','contract','config','revision','capture'])
def test_archived_gpu_source_must_match_exact_restored_binding(candidate,monkeypatch,fault):
    e=candidate;review=prepare(e);binding=review['binding']
    main=e['c'].root;ref=binding['revision_template']
    plan=dict(source_capture=binding['source_capture'],revision=ref['path'],inputs={
        str(main/'contract.json'):binding['main_contract_sha256'],str(main/'main.json'):binding['main_config_sha256'],ref['path']:ref['sha256']})
    if fault=='contract':plan['inputs'][str(main/'contract.json')]='0'*64
    elif fault=='config':plan['inputs'][str(main/'main.json')]='0'*64
    elif fault=='revision':plan['revision']=str(e['pool']/'other.json')
    elif fault=='capture':plan['source_capture']={}
    from types import SimpleNamespace
    monkeypatch.setattr(m,'ArchivedSnapshot',lambda *a:SimpleNamespace(read=lambda p:{'plan':plan},unchanged=lambda:None))
    # Access the genuine implementation, temporarily replaced only by the
    # publication fixture's synthetic GPU proof adapter.
    import importlib.util
    spec=importlib.util.spec_from_file_location('publisher_source_guard_test',resolve(m.OWN));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'ArchivedSnapshot',m.ArchivedSnapshot)
    if fault=='none':module.tested_source(binding)
    else:
        with pytest.raises(ValueError,match='another source'):module.tested_source(binding)
