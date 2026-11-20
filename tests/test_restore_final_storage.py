"""NFS transaction/lineage fixtures and real process/lock guards; no GPU proof."""
from copy import deepcopy
import fcntl
import json
from pathlib import Path
import subprocess
import sys

import pytest
from common.data_contracts import atomic_json,sha256_file
from scripts import restore_final_storage as module
from scripts.training_stage_storage_revision import revised_contract,DEFERRED,SCOPE
from test_independent_checkpoint_handoff import case
from test_latest_independent_revision import initialize,add_checkpoint


@pytest.fixture
def setup(case,monkeypatch):
    c=case;pool=initialize(c,monkeypatch)
    c.contract.update(target_steps=57000,tiered_retention=dict(nfs_keep=3,local_keep=15,
        adaptive_local_pruning=True,resume_from_local=False))
    for name,gib in zip(DEFERRED,(8,1,1,100)):
        c.contract['shared_storage']['nfs_pools'][name]=dict(paths=[str(pool/name)],budget_bytes=gib*2**30)
    approval=dict(authorized=True,scope=SCOPE,target_step=57000,resume_step=10500,
        deferred_pool_names=list(DEFERRED),user_decision='restore original pools before final work',
        restore_deferred_budget_before_final_work=True)
    auth=pool/'authorization.json';atomic_json(auth,approval)
    c.contract=revised_contract(c.contract,approval)
    c.contract['storage_phase_revision']['authorization']=str(auth)
    c.contract['evidence'][str(auth)]=sha256_file(auth)
    atomic_json(c.root/'contract.json',c.contract)
    c.revision['source_contract_sha256']=sha256_file(c.root/'contract.json');atomic_json(c.review_path,c.revision)
    add_checkpoint(c,10500);(c.root/'training/log.jsonl').write_text('{"step":10500}\n')
    roles=['supervisor','worker',*[f'rank{i}' for i in range(8)]]
    capture=dict(status='observed_live_not_stopped',run=str(c.root),
        processes={role:dict(pid=1000000300+i,start_ticks=1) for i,role in enumerate(roles)})
    observer=dict(pid=1000000400,start_ticks=1,argv=['scripts/observe_extended_training_v2.py','--source-run',str(c.root)])
    monkeypatch.setattr(module,'capacity',lambda *a:{'fixture_only_no_second1024GiB_reservation':True})
    return dict(c=c,pool=pool,root=pool/'restoration',capture=capture,observer=observer,auth=auth)


def prepare(e):
    c=e['c'];return module.prepare(c.root,e['root'],c.review_path,e['capture'],e['observer'])


def test_read_only_preparation_then_restore_and_fresh_nfs_revision(setup):
    e=setup;c=e['c'];original=(c.root/'contract.json').read_bytes();config=(c.root/'main.json').read_bytes()
    plan=prepare(e);assert (c.root/'contract.json').read_bytes()==original
    candidate=json.loads((e['root']/'candidate_contract.json').read_text())
    delta=deepcopy(candidate);delta.pop('final_storage_restoration')
    for n in DEFERRED:delta['shared_storage']['nfs_pools'][n]=c.contract['shared_storage']['nfs_pools'][n]
    assert delta==c.contract
    report=module.publish(e['root']);assert report['step']==10500
    assert report['actual8gpu_verified'] is False and report['production_adopted'] is False
    assert sha256_file(c.root/'contract.json')==plan['candidate_contract_sha256']
    assert (e['root']/'previous_contract.json').read_bytes()==original
    assert (c.root/'main.json').read_bytes()==config and c.parent.exists()
    revision=json.loads(Path(report['revision']).read_text())
    assert revision['source_contract_sha256']==plan['candidate_contract_sha256']
    review=json.loads((Path(report['revision']).parent/'review.json').read_text())
    assert review['review']['checkpoint']['full_payload_sha_refreshed']
    assert module.publish(e['root'])==report


@pytest.mark.parametrize('fault',['authorization','budget','path','existing','reserve','temporary','retention','repeat'])
def test_invalid_restore_candidate_rejected_before_preparation(setup,fault):
    e=setup;c=e['c'];base=deepcopy(c.contract);spec=base['shared_storage'];phase=base['storage_phase_revision']
    if fault=='authorization':atomic_json(e['auth'],{'authorized':False})
    if fault=='budget':phase['deferred_pools']['hf_and_serving']['budget_bytes']=200*2**30
    if fault=='path':spec['nfs_pools']['final_native']['paths']=[str(c.root)]
    if fault=='existing':Path(spec['nfs_pools']['final_native']['paths'][0]).mkdir()
    if fault=='reserve':spec['reserve_bytes']=199*2**30
    if fault=='temporary':spec['temporary_budget_bytes']=1000*2**30
    if fault=='retention':base['tiered_retention']['nfs_keep']=2
    if fault=='repeat':base['final_storage_restoration']={}
    with pytest.raises(ValueError):module.candidate_contract(base,sha256_file(c.root/'contract.json'))
    assert not e['root'].exists()


@pytest.mark.parametrize('fault',['candidate','code','template','source','config','marker','unapproved_diff'])
def test_changed_plan_or_source_never_publishes(setup,fault):
    e=setup;c=e['c'];prepare(e);before=(c.root/'contract.json').read_bytes()
    if fault=='candidate':(e['root']/'candidate_contract.json').write_text('{}')
    if fault=='code':
        p=e['root']/'plan.json';plan=json.loads(p.read_text());plan['code']['scripts/restore_final_storage.py']='0'*64
        atomic_json(p,plan);atomic_json(e['root']/'prepared.json',{'plan_sha256':sha256_file(p)})
    if fault=='template':(e['root']/'rebased_template.json').write_text('{}')
    if fault=='source':(c.root/'contract.json').write_text(before.decode()+' ')
    if fault=='config':(c.root/'main.json').write_text('{}')
    if fault=='marker':atomic_json(e['root']/'prepared.json',{})
    if fault=='unapproved_diff':
        p=e['root']/'candidate_contract.json';candidate=json.loads(p.read_text());candidate['target_steps']=35000;atomic_json(p,candidate)
        plan=json.loads((e['root']/'plan.json').read_text());plan['candidate_contract_sha256']=sha256_file(p);plan['inputs'][str(p)]=sha256_file(p)
        atomic_json(e['root']/'plan.json',plan);atomic_json(e['root']/'prepared.json',{'plan_sha256':sha256_file(e['root']/'plan.json')})
    current=(c.root/'contract.json').read_bytes()
    with pytest.raises((ValueError,KeyError)):module.publish(e['root'])
    assert (c.root/'contract.json').read_bytes()==current
    assert not (e['root']/'publication_intent.json').exists()


@pytest.mark.parametrize('fault',['live_observer','new_consumer','live_rank','controller_lock','published_runtime','off_boundary','space'])
def test_live_process_locks_space_and_boundary_prevent_mutation(setup,monkeypatch,fault):
    e=setup;c=e['c'];proc=None;lock=None
    try:
        if fault in ('live_observer','new_consumer','live_rank'):
            args=[sys.executable,'-c','import time;time.sleep(30)']
            if fault=='new_consumer':args+=['--source-run',str(c.root)]
            proc=subprocess.Popen(args)
            ticks=int(Path(f'/proc/{proc.pid}/stat').read_text().rsplit(')',1)[1].split()[19])
            if fault=='live_observer':e['observer'].update(pid=proc.pid,start_ticks=ticks)
            if fault=='live_rank':e['capture']['processes']['rank7'].update(pid=proc.pid,start_ticks=ticks)
        prepare(e);before=(c.root/'contract.json').read_bytes()
        if fault=='controller_lock':
            lock=(c.root/'controller.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if fault=='published_runtime':atomic_json(e['pool']/'independent/runtime_contract.json',{})
        if fault=='off_boundary':(c.root/'training/log.jsonl').write_text('{"step":10510}\n')
        if fault=='space':
            def reject(*a):raise RuntimeError('space exhausted')
            monkeypatch.setattr(module,'capacity',reject)
        with pytest.raises((ValueError,RuntimeError,BlockingIOError)):module.publish(e['root'])
        assert (c.root/'contract.json').read_bytes()==before
        assert not (e['root']/'publication_intent.json').exists()
    finally:
        if lock:lock.close()
        if proc:proc.terminate();proc.wait(timeout=5)


@pytest.mark.parametrize('point',['before_contract','after_contract','after_revision'])
def test_interrupted_transaction_preserves_evidence_and_explicit_resume(setup,point):
    e=setup;plan=prepare(e);original=(e['c'].root/'contract.json').read_bytes()
    def crash(where):
        if where==point:raise RuntimeError('injected interruption')
    with pytest.raises(RuntimeError,match='injected'):module.publish(e['root'],fault=crash)
    assert not (e['root']/'.done').exists()
    assert (e['root']/'previous_contract.json').read_bytes()==original
    report=module.publish(e['root'])
    assert report['contract_sha256']==plan['candidate_contract_sha256']


def test_partial_revision_preserved_and_never_counted_complete(setup):
    e=setup;prepare(e);(e['root']/'latest_revision').mkdir();(e['root']/'latest_revision/failure_evidence').write_text('keep')
    with pytest.raises(FileNotFoundError):module.publish(e['root'])
    assert not (e['root']/'.done').exists()
    assert (e['root']/'latest_revision/failure_evidence').read_text()=='keep'
