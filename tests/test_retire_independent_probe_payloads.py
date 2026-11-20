"""Real file/SHA transactions; synthetic accepted GPU evidence is explicit."""
from copy import deepcopy
import fcntl
import hashlib
import json
from pathlib import Path
import tempfile

import pytest
from common.data_contracts import atomic_json,resolve,sha256_file
from train.final_resume_probe import file_stats
from scripts import retire_independent_probe_payloads as module
from test_sealed_independent_resume_result import diagnostic
from test_independent_training_audit import case


@pytest.fixture
def candidate(monkeypatch):
    with tempfile.TemporaryDirectory(dir=resolve('runs'),prefix='probe-retire-') as nfs,tempfile.TemporaryDirectory(prefix='probe-retire-local-') as local:
        base=Path(nfs);main=base/'main';root=base/'diagnostic';root.mkdir();main.mkdir();temp=Path(local);scratch=temp/root.name
        contract=dict(tiered_retention=dict(temp_root=str(temp)),shared_storage=dict(local_temp_paths=[str(temp)],
            nfs_pools=dict(transition=dict(paths=[str(base)],budget_bytes=2**30))))
        atomic_json(main/'contract.json',contract)
        spec=dict(plan=dict(main_run=str(main),run_dir=str(root),scratch=str(scratch)))
        atomic_json(root/'spec.json',spec);sources=[root/'spec.json']
        for name in module.OUTPUTS:
            cp=scratch/name;cp.mkdir(parents=True)
            atomic_json(cp/'meta.json',dict(kind='synthetic_fixture',step=1))
            for f in ['.complete','.metadata',*[f'rng_rank{i}.pt' for i in range(8)],*[f'__{i}_0.distcp' for i in range(8)]]:(cp/f).write_bytes(('synthetic '+f).encode())
            rows=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(cp.iterdir())]
            cert=dict(path=str(cp),files=rows,bytes=sum(r['bytes'] for r in rows),stats=file_stats(cp),metadata=json.loads((cp/'meta.json').read_text()))
            p=root/'certificates'/(name+'.json');atomic_json(p,cert);sources.append(p)
        archive=root/'accepted_evidence';blobs=archive/'blobs';blobs.mkdir(parents=True);manifest={'files':{}}
        for p in sources:
            raw=p.read_bytes();slot=hashlib.sha256(str(p).encode()).hexdigest();(blobs/slot).write_bytes(raw)
            manifest['files'][str(p)]=dict(blob=slot,bytes=len(raw),sha256=sha256_file(p))
        atomic_json(archive/'manifest.json',manifest)
        accepted=dict(status='verified_historical_independent8gpu_resume_evidence',target_steps=57000,synthetic_fixture=True)
        atomic_json(archive/'verified.json',accepted)
        for name in ['manifest.json','verified.json']:atomic_json(root/'frozen_prefix'/name,{'fixture':True})
        monkeypatch.setattr(module,'verify',lambda _:deepcopy(accepted))
        monkeypatch.setattr(module,'capacity',lambda *a:None)
        yield dict(root=root,main=main,scratch=scratch,accepted=accepted)


def approve(root):
    out=root/'payload_retirement';auth=out/'authorization.json'
    atomic_json(auth,dict(purpose='retire_completed_independent_probe_payloads_v1',authorized=True,
        plan_sha256=sha256_file(out/'plan.json'),user_decision='explicit fixture deletion permission'))
    return auth


def test_exact_four_states_retired_with_nfs_metadata_rng_preserved(candidate):
    e=candidate;root=e['root'];original={p.name:sha256_file(p) for p in (root/'accepted_evidence').glob('*.json')}
    plan=module.prepare(root);assert plan['authorized'] is False and len(plan['entries'])==4
    assert len(plan['metadata_backups'])==44
    result=module.execute(root,approve(root))
    assert result['retired_bytes']==plan['source_bytes'] and not e['scratch'].exists()
    assert result['training_checkpoint_deleted'] is False and result['failed_diagnostic_deleted'] is False
    assert original=={p.name:sha256_file(p) for p in (root/'accepted_evidence').glob('*.json')}
    assert module.execute(root,root/'payload_retirement/authorization.json')==result
    for path,row in plan['metadata_backups'].items():assert sha256_file(path)==row['sha256']


@pytest.mark.parametrize('fault',['not_accepted','wrong_target','unknown_output','payload','metadata','live_worker','wrong_scratch'])
def test_bad_diagnostic_cannot_be_proposed(candidate,monkeypatch,fault):
    e=candidate;root=e['root']
    if fault=='not_accepted':e['accepted']['status']='failed'
    if fault=='wrong_target':e['accepted']['target_steps']=35000
    if fault=='unknown_output':(e['scratch']/'extra').mkdir()
    if fault=='payload':(e['scratch']/'restored_parent/__0_0.distcp').write_bytes(b'changed')
    if fault=='metadata':(e['scratch']/'restored_parent/meta.json').write_text('{}')
    if fault=='live_worker':monkeypatch.setattr(module,'owned_processes',lambda _: [999999])
    if fault=='wrong_scratch':
        c=json.loads((e['main']/'contract.json').read_text());c['tiered_retention']['temp_root']=str(e['scratch']);atomic_json(e['main']/'contract.json',c)
    with pytest.raises(ValueError):module.prepare(root)
    assert not (root/'payload_retirement').exists()
    assert all((e['scratch']/name).exists() for name in module.OUTPUTS)


@pytest.mark.parametrize('fault',['missing','false','wrong_plan','backup','code','live_worker','lock','capacity'])
def test_missing_approval_or_unsafe_execution_leaves_payloads(candidate,monkeypatch,fault):
    e=candidate;root=e['root'];plan=module.prepare(root);auth=approve(root);lock=None
    try:
        if fault=='missing':auth.unlink()
        if fault in ('false','wrong_plan'):
            a=json.loads(auth.read_text());a['authorized' if fault=='false' else 'plan_sha256']=False if fault=='false' else '0'*64;atomic_json(auth,a)
        if fault=='backup':Path(next(iter(plan['metadata_backups']))).write_bytes(b'corrupt')
        if fault=='code':
            plan['code'][module.CODE[0]]='0'*64;atomic_json(root/'payload_retirement/plan.json',plan)
        if fault=='live_worker':monkeypatch.setattr(module,'owned_processes',lambda _: [999999])
        if fault=='lock':lock=(root/'controller.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if fault=='capacity':
            def fail(*a):raise RuntimeError('capacity unavailable')
            monkeypatch.setattr(module,'capacity',fail)
        with pytest.raises((ValueError,RuntimeError,FileNotFoundError,BlockingIOError)):module.execute(root,auth)
        assert all((e['scratch']/name).exists() for name in module.OUTPUTS)
        assert not (root/'payload_retirement/.done').exists()
    finally:
        if lock:lock.close()


@pytest.mark.parametrize('point',['renamed_source','retired_file'])
def test_interrupted_cleanup_resumes_only_same_approved_inventory(candidate,point):
    e=candidate;root=e['root'];module.prepare(root);auth=approve(root);hit=[]
    def stop(where):
        if where==point and not hit:hit.append(where);raise RuntimeError('injected interruption')
    with pytest.raises(RuntimeError,match='injected'):module.execute(root,auth,fault=stop)
    assert not (root/'payload_retirement/.done').exists()
    result=module.execute(root,auth)
    assert not e['scratch'].exists() and len(result['entries'])==4


def test_unknown_transaction_file_is_preserved(candidate):
    e=candidate;root=e['root'];module.prepare(root);auth=approve(root)
    def stop(where):
        if where=='renamed_source':raise RuntimeError('interrupt')
    with pytest.raises(RuntimeError):module.execute(root,auth,fault=stop)
    p=e['scratch']/'.retiring_restored_parent'/'foreign';p.write_text('keep')
    with pytest.raises(ValueError,match='Unowned'):module.execute(root,auth)
    assert p.read_text()=='keep'


def test_historical_evidence_change_during_retirement_stops_before_next_state(candidate):
    e=candidate;root=e['root'];module.prepare(root);auth=approve(root);changed=[]
    def mutate(where):
        if where=='retired_file' and not changed:
            p=next((root/'accepted_evidence/blobs').iterdir());p.write_bytes(b'corrupt');changed.append(p)
    with pytest.raises(ValueError,match='evidence changed'):module.execute(root,auth,fault=mutate)
    assert not (root/'payload_retirement/.done').exists()
    assert (e['scratch']/'continuous_final').exists()


def test_real_historical_replay_survives_actual_fixture_payload_retirement(diagnostic,monkeypatch):
    # Existing diagnostic fixture uses a different path layout, so only layout
    # admission is substituted here. Seal/replay24rank/lineage/cert proofs are real.
    from scripts.seal_independent_resume_result import seal,verify
    f,c,_=diagnostic;accepted=seal(f.root);root=f.root;scratch=Path(f.plan['scratch'])
    def actual_evidence(_):
        assert verify(root)==accepted
        certs={name:json.loads((root/'certificates'/(name+'.json')).read_text()) for name in module.OUTPUTS}
        return c.root,c.contract,scratch,certs,accepted
    monkeypatch.setattr(module,'details',actual_evidence);monkeypatch.setattr(module,'capacity',lambda *a:None)
    plan=module.prepare(root);result=module.execute(root,approve(root))
    assert result['retired_bytes']==plan['source_bytes'] and not scratch.exists()
    assert verify(root)==accepted
    assert c.cp.exists() and c.parent.exists()
