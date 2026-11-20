"""Actual sealed9500 lineage plus synthetic suffix, never GPU acceptance."""
import json
from pathlib import Path
import shutil
import tempfile

import pytest
from common.data_contracts import atomic_json,sha256_file,resolve
from scripts.audit_independent_training import audit_run
from scripts.freeze_independent_prefix import freeze,replay
from test_independent_training_audit import case


def captured(main):
    return dict(status='observed_live_not_stopped',run=str(main),processes={
        name:dict(pid=90000000+i,start_ticks=1)
        for i,name in enumerate(['supervisor','worker',*[f'rank{r}' for r in range(8)]])})


def prepare(case,directory):
    audit=audit_run(case.root,10000,case.revision_path)
    p=case.root/'prefix_audit.json';atomic_json(p,audit)
    result=freeze(p,directory,captured(case.root))
    return p,audit,result


def test_replay_after_source_logs_advance_and_original_payload_loss(case):
    with tempfile.TemporaryDirectory(dir=resolve('runs')) as tmp:
        out=Path(tmp)/'frozen';p,audit,first=prepare(case,out)
        assert first['counts']==audit['counts']
        assert not first['actual8gpu_verified'] and not first['current_source_verified']
        (case.root/'training/log.jsonl').write_text('changed after resume\n')
        shutil.rmtree(case.root/'ledgers');shutil.rmtree(case.cp);shutil.rmtree(case.parent)
        assert replay(out)==first
        assert sum(x.stat().st_size for x in (out/'blobs').iterdir())<192*2**20


@pytest.mark.parametrize('fault',['blob','missing_rank','path_escape','forged_counts','rng','extra_blob'])
def test_corrupt_archive_cannot_replay(case,fault):
    with tempfile.TemporaryDirectory(dir=resolve('runs')) as tmp:
        out=Path(tmp)/'frozen';p,audit,_=prepare(case,out)
        m=json.loads((out/'manifest.json').read_text())
        if fault=='blob':next((out/'blobs').iterdir()).write_bytes(b'corrupt')
        if fault=='missing_rank':
            k=next(k for k in m['files'] if '/ledgers/' in k);del m['files'][k]
        if fault=='path_escape':next(iter(m['files'].values()))['blob']='../manifest.json'
        if fault=='forged_counts':
            row=m['files'][str(p)];dest=out/'blobs'/row['blob'];fake=json.loads(dest.read_text())
            fake['counts']['committed_tokens']+=2097152;atomic_json(dest,fake)
            row.update(bytes=dest.stat().st_size,sha256=sha256_file(dest))
        if fault=='rng':
            k=next(k for k in m['files'] if k.endswith('/rng_rank7.pt'));row=m['files'][k]
            dest=out/'blobs'/row['blob'];dest.write_bytes(b'X'*dest.stat().st_size)
            row['sha256']=sha256_file(dest)
        if fault=='extra_blob':(out/'blobs'/'unowned').write_bytes(b'unknown')
        atomic_json(out/'manifest.json',m)
        with pytest.raises((ValueError,KeyError,json.JSONDecodeError)):replay(out)


def test_changed_source_before_freeze_and_existing_namespace_rejected(case):
    with tempfile.TemporaryDirectory(dir=resolve('runs')) as tmp:
        audit=audit_run(case.root,10000,case.revision_path)
        p=case.root/'prefix_audit.json';atomic_json(p,audit)
        (case.root/'training/log.jsonl').write_text('changed\n')
        out=Path(tmp)/'frozen'
        with pytest.raises(ValueError,match='changed'):freeze(p,out,captured(case.root))
        assert out.exists() and not (out/'verified.json').exists()
        with pytest.raises(ValueError,match='Preserve'):freeze(p,out,captured(case.root))


@pytest.mark.parametrize('failure',[False,True])
def test_controller_materializes_only_after_frozen_replay(case,monkeypatch,failure):
    from scripts import run_independent_resume as controller
    # The historical fixture has boundary9500->10000. Process/input/CUDA
    # admission is synthetic here; real lineage, trace, SHA and replay run.
    c=case
    plan=dict(main_run=str(c.root),parent_step=10000,revision=str(c.revision_path),source_capture=captured(c.root))
    monkeypatch.setattr(controller,'stopped_source',lambda p:(c.root,c.contract))
    monkeypatch.setattr(controller,'fresh_inputs',lambda *a:{})
    monkeypatch.setattr(controller,'check_spec',lambda s:(plan,c.config,c.candidate))
    if failure:
        def fail(*a):raise RuntimeError('injected frozen publication failure')
        monkeypatch.setattr('scripts.freeze_independent_prefix.freeze',fail)
    with tempfile.TemporaryDirectory(dir=resolve('runs')) as tmp:
        root=Path(tmp)
        if failure:
            with pytest.raises(RuntimeError,match='publication'):controller.materialize_spec(root,plan)
            assert not (root/'spec.json').exists()
            assert (root/'independent_training_audit.json').exists()
        else:
            spec=controller.materialize_spec(root,plan)
            assert spec['frozen_prefix']['manifest_sha256']==sha256_file(root/'frozen_prefix/manifest.json')
            assert replay(root/'frozen_prefix')['counts']['committed_tokens']==10000*2097152
