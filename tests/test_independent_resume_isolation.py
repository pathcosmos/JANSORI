"""Real NFS/local DAC + crash recovery on tiny owned fixtures; no main mutation."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

import pytest

from common.data_contracts import resolve,atomic_json,sha256_file
from scripts import independent_resume_isolation as iso
from scripts.tiered_checkpoint_retention import size_tree


@pytest.fixture
def fixture(monkeypatch):
    with tempfile.TemporaryDirectory(dir=resolve('runs')) as nf,tempfile.TemporaryDirectory() as lf:
        base=Path(nf);local=Path(lf);main=base/'main';prior=base/'prior';transition=base/'transition'
        for p in (main,prior,transition):p.mkdir()
        parent=main/'parent_checkpoint/ckpt_0010000';old=prior/'parent_checkpoint/ckpt_0008000'
        history=local/'history1/checkpoints/ckpt_0008000';proof=local/'proof/training_contract.json'
        for p in (parent,old,history):
            p.mkdir(parents=True);(p/'payload').write_bytes(b'stateful-model-optimizer-rng');(p/'.complete').touch()
        proof.parent.mkdir();proof.write_text('{"old":"proof"}\n')
        atomic_json(main/'parent_handoff.json',dict(source_run=str(prior),parent_checkpoint=str(parent)))
        atomic_json(prior/'parent_handoff.json',dict(parent_checkpoint=str(old)))
        c=dict(expected_checkpoint_bytes=1024,shared_storage={
            'nfs_pools':{'transition':{'paths':[str(transition)],'budget_bytes':2**30},
                'training':{'paths':[str(main),str(prior)],'budget_bytes':128*2**20}},
            'reserve_bytes':200*2**30,'temporary_budget_bytes':1024*2**30,
            'local_history_paths':[str(local/'history1'),str(local/'history2')],
            'local_temp_paths':[str(local/'proof')],'local_history_budget_bytes':2**30},
            retention_branches=[dict(first_step=500,last_step=8000,policy={'local_root':str(local/'history1')}),
                dict(first_step=8500,last_step=10000,policy={'local_root':str(local/'history2')})])
        atomic_json(main/'contract.json',c);atomic_json(main/'main.json',{'fixture':True})
        revision=base/'revision.json';atomic_json(revision,dict(local_proof_backups={str(proof):{}}))
        capture=base/'capture.json';atomic_json(capture,dict(status='observed_live_not_stopped',run=str(main),
            processes={name:dict(pid=90000000+i,start_ticks=1) for i,name in enumerate(['supervisor','worker',*[f'rank{r}' for r in range(8)]])}))
        monkeypatch.setattr('train.independent_checkpoint_handoff.review_handoff',lambda *a:{})
        root=transition/'isolation';diag=transition/'diagnostic'
        data=dict(base=base,local=local,main=main,root=root,diag=diag,revision=revision,capture=capture,parent=parent,proof=proof,history=history)
        try:yield data
        finally:
            # Test fixture cleanup only; failed production artifacts are retained.
            for start in (base,local):
                for directory,dirs,files in os.walk(start):
                    os.chmod(directory,0o700)
                    for name in dirs:
                        p=Path(directory)/name
                        if not p.is_symlink():os.chmod(p,0o700)


def prepare(f):
    return iso.build_plan(f['main'],f['root'],f['diag'],f['revision'],f['capture'],authorized=True)


def fail_at(stage):
    def fault(point):
        if point==stage:raise RuntimeError('injected '+stage)
    return fault


def test_real_two_filesystem_dac_roundtrip_and_exact_payload_stats(fixture):
    f=fixture;plan=prepare(f);original={e['path']:e['payload'] for e in plan['entries']}
    assert f['parent'].stat().st_dev!=f['history'].stat().st_dev
    physical=sum(size_tree(p) for p in (f['main']/'parent_checkpoint',f['local']/'history1'))
    proof=iso.enter(f['root']);assert proof==iso.verify_isolated(f['root'])
    for e in plan['entries']:
        if e['payload']:
            with pytest.raises(PermissionError):os.listdir(e['path'])
    accounted=sum(iso.accounted_size(p,f['root']) for p in (f['main']/'parent_checkpoint',f['local']/'history1'))
    assert accounted==physical and sum(size_tree(p) for p in (f['main']/'parent_checkpoint',f['local']/'history1'))==0
    assert iso.enter(f['root'])==proof
    result=iso.restore(f['root']);assert result['payload_deleted'] is False
    assert iso.restore(f['root'])==result
    historical=iso.verify_isolation_record(f['root'])
    assert historical['current_unavailability_verified'] is False and historical['actual8gpu_verified'] is False
    for e in plan['entries']:
        iso.verify_readable(Path(e['path']),original[e['path']],hashes=True)
    with pytest.raises(ValueError):iso.verify_isolated(f['root'])
    with pytest.raises(ValueError,match='reused'):iso.enter(f['root'])


@pytest.mark.parametrize('stage',['block_intent','blocked_before_ack'])
def test_partial_enter_can_restore_without_overwrite(fixture,stage):
    f=fixture;plan=prepare(f)
    with pytest.raises(RuntimeError,match='injected'):iso.enter(f['root'],fault=fail_at(stage))
    assert not (f['root']/'isolated.json').exists()
    iso.restore(f['root'])
    for e in plan['entries']:iso.verify_readable(Path(e['path']),e['payload'],hashes=True)


@pytest.mark.parametrize('stage',['restore_intent','unblocked','restored_before_ack'])
def test_partial_restore_is_idempotent(fixture,stage):
    f=fixture;plan=prepare(f);iso.enter(f['root'])
    with pytest.raises(RuntimeError,match='injected'):iso.restore(f['root'],fault=fail_at(stage))
    iso.restore(f['root'])
    for e in plan['entries']:iso.verify_readable(Path(e['path']),e['payload'],hashes=True)


def test_real_sigkill_after_directory_chmod_recovers(fixture):
    f=fixture;plan=prepare(f)
    code="""import os,signal,sys
from scripts.independent_resume_isolation import enter
def fault(point):
    if point=='blocked_before_ack':os.kill(os.getpid(),signal.SIGKILL)
enter(sys.argv[1],fault=fault)
"""
    p=subprocess.run([sys.executable,'-c',code,str(f['root'])],capture_output=True,timeout=30,
        env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1'))
    assert p.returncode==-signal.SIGKILL,p.stderr.decode()
    # Resume enter rehashes the unacknowledged quarantine before denying it.
    iso.enter(f['root']);iso.verify_isolated(f['root']);iso.restore(f['root'])
    for e in plan['entries']:iso.verify_readable(Path(e['path']),e['payload'],hashes=True)


def test_changed_permissions_and_replaced_root_refused(fixture):
    f=fixture;plan=prepare(f);iso.enter(f['root'])
    os.chmod(f['parent'],0o700)
    with pytest.raises(ValueError,match='changed'):iso.verify_isolated(f['root'])
    with pytest.raises(ValueError,match='changed|mutated'):iso.restore(f['root'])


def test_active_reader_and_live_diagnostic_prevent_mutation(fixture):
    f=fixture;plan=prepare(f);ready=f['base']/'reader.ready'
    code="import sys,time;from pathlib import Path;f=open(sys.argv[1],'rb');Path(sys.argv[2]).touch();time.sleep(30)"
    p=subprocess.Popen([sys.executable,'-c',code,str(f['parent']/'payload'),str(ready)])
    import time
    try:
        until=time.monotonic()+5
        while not ready.exists() and time.monotonic()<until:time.sleep(.01)
        assert ready.exists()
        with pytest.raises(RuntimeError,match='active file handle'):iso.enter(f['root'])
        assert (f['parent']/'payload').exists()
    finally:p.terminate();p.wait(timeout=5)
    iso.restore(f['root'])
    # A separately alive diagnostic also prevents any isolation mutation.
    p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)',
        'train.independent_resume_probe','--spec',str(f['diag']/'spec.json')])
    try:
        with pytest.raises(RuntimeError,match='Diagnostic processes'):iso.restore(f['root'])
        assert p.poll() is None
    finally:p.terminate();p.wait(timeout=5)


def test_hardlink_and_symlink_refusal(fixture):
    f=fixture;payload=f['parent']/'payload';alias=f['base']/'alias';os.link(payload,alias)
    with pytest.raises(ValueError,match='Hard-linked'):prepare(f)
    alias.unlink();(f['parent']/'alias').symlink_to(payload)
    with pytest.raises(ValueError,match='symlink'):prepare(f)
    (f['parent']/'alias').unlink()


def test_changed_payload_and_missing_authorization_fail_before_publication(fixture):
    f=fixture
    with pytest.raises(ValueError,match='authorization'):
        iso.build_plan(f['main'],f['root'],f['diag'],f['revision'],f['capture'])
    prepare(f);payload=f['parent']/'payload';payload.write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'):iso.enter(f['root'])
    assert payload.read_bytes()==b'changed' and not (f['root']/'isolated.json').exists()


def test_actual_restore_sigkill_can_finish_without_payload_changes(fixture):
    f=fixture;plan=prepare(f);iso.enter(f['root'])
    code="""import os,signal,sys
from scripts.independent_resume_isolation import restore
def fault(point):
    if point=='unblocked':os.kill(os.getpid(),signal.SIGKILL)
restore(sys.argv[1],fault=fault)
"""
    p=subprocess.run([sys.executable,'-c',code,str(f['root'])],capture_output=True,timeout=30,
        env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1'))
    assert p.returncode==-signal.SIGKILL,p.stderr.decode()
    iso.restore(f['root'])
    for e in plan['entries']:iso.verify_readable(Path(e['path']),e['payload'],hashes=True)


def test_live_source_refused_before_any_transaction(fixture):
    f=fixture;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
    try:
        ticks=int(Path(f'/proc/{p.pid}/stat').read_text().rsplit(')',1)[1].split()[19])
        capture=json.loads(f['capture'].read_text());capture['processes']['supervisor']=dict(pid=p.pid,start_ticks=ticks)
        atomic_json(f['capture'],capture)
        with pytest.raises(ValueError,match='still present'):prepare(f)
        assert not f['root'].exists() and p.poll() is None
        assert (f['parent']/'payload').read_bytes()==b'stateful-model-optimizer-rng'
    finally:p.terminate();p.wait(timeout=5)


def test_original_storage_validator_counts_hidden_bytes_and_hook_reverts(fixture,monkeypatch):
    from types import SimpleNamespace
    from scripts import extended_horizon_storage as storage
    f=fixture;plan=prepare(f);c=json.loads((f['main']/'contract.json').read_text())
    monkeypatch.setattr(storage.shutil,'disk_usage',lambda p:SimpleNamespace(free=5*1024*2**30))
    before=storage.enforce_shared_space(f['main'],c);iso.enter(f['root']);original=storage.size_tree
    raw=storage.enforce_shared_space(f['main'],c)
    assert raw['local_history_used_bytes']<before['local_history_used_bytes']
    with pytest.raises(RuntimeError,match='fixture failure'):
        with iso.accounting_view(f['root']):
            with iso.accounting_view(f['root']):
                fixed=storage.enforce_shared_space(f['main'],c)
            assert fixed['local_history_used_bytes']==before['local_history_used_bytes']
            assert fixed['local_temporary_used_bytes']==before['local_temporary_used_bytes']
            assert fixed['nfs_used_by_path'][str(f['main'])]==before['nfs_used_by_path'][str(f['main'])]
            assert fixed['local_free_bytes']==raw['local_free_bytes']  # No synthetic free-space credit.
            raise RuntimeError('fixture failure')
    assert storage.size_tree is original
    iso.restore(f['root'])
