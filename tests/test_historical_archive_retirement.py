import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest

from scripts import archive_retire_historical_checkpoints as a


@pytest.fixture
def case(tmp_path):
    with tempfile.TemporaryDirectory(dir='/dev/shm',prefix='historical-archive-test-') as local:
        src=tmp_path/'runs/old_diagnostic/ckpt_0000004';src.mkdir(parents=True)
        for n in ['.metadata','.complete','meta.json','__0_0.distcp','rng_rank0.pt']:
            (src/n).write_bytes((n+' original').encode()*1024)
        slot=hashlib.sha256(str(src).encode()).hexdigest()[:24]
        dest=Path(local)/slot;state=tmp_path/'receipts'/slot;state.mkdir(parents=True)
        stats=a.snapshot(src)
        entry=dict(slot=slot,source=str(src),destination=str(dest),source_stats=stats,
                   bytes=sum(v[2] for v in stats.values()))
        yield entry,state,src,dest


def pulse(*args,**kwargs):pass


def test_copy_retire_exact_bytes_and_durable_metadata(case):
    e,s,src,dst=case;original={p.name:p.read_bytes() for p in src.iterdir()}
    a.archive_one(e,s,pulse)
    result=a.retire_one(e,s,pulse,readers=lambda _:None)
    assert result['status']=='retired_nfs_verified_local_preserved'
    assert not src.exists()
    assert {p.name:p.read_bytes() for p in dst.iterdir()}==original
    assert not (s/'source_metadata/.complete').exists()
    assert len(list((s/'source_metadata').iterdir()))==4
    assert a.retire_one(e,s,pulse,readers=lambda _:None)==result


@pytest.mark.parametrize('point',['copied_file','verified_copy','published_before_ack'])
def test_copy_interruption_recovery(case,point):
    e,s,src,dst=case
    def fault(p):
        if p==point:raise InterruptedError(p)
    with pytest.raises(InterruptedError):a.archive_one(e,s,pulse,fault=fault)
    assert src.exists() and not (s/'copy_ack.json').exists()
    a.archive_one(e,s,pulse)
    a.retire_one(e,s,pulse,readers=lambda _:None)
    assert not src.exists() and dst.exists()


@pytest.mark.parametrize('point',['renamed_source','retired_file'])
def test_retirement_interruption_recovery(case,point):
    e,s,src,dst=case;a.archive_one(e,s,pulse)
    def fault(p):
        if p==point:raise InterruptedError(p)
    with pytest.raises(InterruptedError):a.retire_one(e,s,pulse,fault=fault,readers=lambda _:None)
    assert not (s/'retired.json').exists()
    a.retire_one(e,s,pulse,readers=lambda _:None)
    assert not src.exists() and dst.exists()


def test_missing_or_corrupt_local_copy_never_retires_source(case):
    e,s,src,dst=case;a.archive_one(e,s,pulse)
    (dst/'__0_0.distcp').write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='SHA/size'):a.retire_one(e,s,pulse,readers=lambda _:None)
    assert src.exists()
    shutil.rmtree(dst)
    with pytest.raises(ValueError,match='Missing'):a.retire_one(e,s,pulse,readers=lambda _:None)
    assert src.exists()


def test_source_change_never_deleted(case):
    e,s,src,dst=case;a.archive_one(e,s,pulse)
    (src/'rng_rank0.pt').write_bytes(b'changed')
    with pytest.raises(ValueError,match='Source changed'):a.retire_one(e,s,pulse,readers=lambda _:None)
    assert src.exists()


def test_unowned_and_unknown_partial_files_preserved(case):
    e,s,src,dst=case;stage=dst.with_name(dst.name+'.partial');stage.mkdir();(stage/'foreign').write_text('keep')
    with pytest.raises(ValueError,match='Unowned'):a.archive_one(e,s,pulse)
    assert (stage/'foreign').read_text()=='keep'


def test_live_reader_blocks_retirement(case):
    e,s,src,dst=case;a.archive_one(e,s,pulse)
    code='import sys,time; f=open(sys.argv[1],"rb"); print("ready",flush=True); time.sleep(30)'
    p=subprocess.Popen([sys.executable,'-c',code,str(src/'__0_0.distcp')],stdout=subprocess.PIPE,text=True)
    try:
        assert p.stdout.readline().strip()=='ready'
        with pytest.raises(RuntimeError,match='active file handle'):a.retire_one(e,s,pulse)
        assert src.exists()
    finally:p.terminate();p.wait(timeout=5)


def test_real_process_death_after_nfs_rename(case):
    e,s,src,dst=case;a.archive_one(e,s,pulse)
    inp=s/'entry_for_test.json';inp.write_text(json.dumps(e))
    code='''
import os,signal,json,sys
from pathlib import Path
from scripts import archive_retire_historical_checkpoints as a
def fault(point):
 if point=='renamed_source':os.kill(os.getpid(),signal.SIGKILL)
a.retire_one(json.loads(Path(sys.argv[1]).read_text()),Path(sys.argv[1]).parent,
 lambda *args,**kw:None,fault=fault,readers=lambda _:None)
'''
    p=subprocess.run([sys.executable,'-c',code,str(inp)],timeout=20)
    assert p.returncode==-9 and not (s/'retired.json').exists()
    a.retire_one(e,s,pulse,readers=lambda _:None)
    assert not src.exists() and dst.exists()


def test_mid_retirement_local_mutation_stops_remaining_deletes(case):
    e,s,src,dst=case;a.archive_one(e,s,pulse)
    def fault(point):
        if point=='retired_file':(dst/'rng_rank0.pt').write_bytes(b'changed')
    with pytest.raises(ValueError,match='Local archive changed'):
        a.retire_one(e,s,pulse,fault=fault,readers=lambda _:None)
    tx=src.with_name('.archive-retiring-'+e['slot'])
    assert tx.exists() and list(tx.iterdir()) and not (s/'retired.json').exists()


def test_plan_protected_source_and_changed_code_are_rejected(case):
    e,s,src,dst=case;project=src.parents[2];main=project/'runs/3b_full_57k_v2';main.mkdir()
    local=dst.parent/'historical_checkpoint_archive_v2'
    execution=project/'runs/3b_57k_transition_v2/archive';execution.mkdir(parents=True)
    contract=dict(shared_storage=dict(local_temp_paths=[str(local.parent)]))
    a.atomic_json(main/'contract.json',contract)
    review=project/'review.json';a.atomic_json(review,dict(other_checkpoints=[dict(path=str(src.relative_to(project)),bytes=e['bytes'])]))
    own='scripts/archive_retire_historical_checkpoints.py';(project/'scripts').mkdir()
    (project/own).write_text('fixture implementation')
    entry=dict(e,destination=str(local/e['slot']))
    plan=dict(purpose='approved_historical_archive_retirement_v1',authorized=True,keep_local_verified_copy=True,
              project_root=str(project),main_run=str(main),review_path=str(review),review_sha256=a.sha256_file(review),
              main_contract_sha256=a.sha256_file(main/'contract.json'),code={own:a.sha256_file(project/own)},
              local_archive_root=str(local),execution_root=str(execution),entries=[entry],copy_workers=4,hash_workers=8,future_temp_bytes=200*a.GIB)
    a.validate_plan(plan)
    plan['authorized']=False
    with pytest.raises(ValueError,match='authorization'):a.validate_plan(plan)
    plan['authorized']=True;plan['entries'][0]['source']=str(main/'parent_checkpoint/ckpt_0010000')
    with pytest.raises(ValueError,match='Protected'):a.validate_plan(plan)
    plan['entries'][0]=entry;plan['code'][own]='bad'
    with pytest.raises(ValueError,match='implementation changed'):a.validate_plan(plan)
