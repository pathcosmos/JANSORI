"""Real tiny-file retirement/crash recovery; no production/GPU evidence claim."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from common.data_contracts import sha256_file
from train.final_resume_probe import file_stats
from scripts.cleanup_budget_probe import retire_entries

@pytest.fixture
def bundle(tmp_path):
    entries=[]
    for i in range(4):
        path=tmp_path/f'new-probe-{i}';path.mkdir();meta=dict(step=16000+i,fixture=True)
        (path/'meta.json').write_text(json.dumps(meta));(path/'payload').write_bytes(bytes([i])*1234)
        files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(path.iterdir())]
        proof=dict(metadata=meta,files=files,bytes=sum(r['bytes'] for r in files),stats=file_stats(path))
        cert=tmp_path/f'certificate{i}.json';cert.write_text(json.dumps(proof))
        entries.append(dict(path=str(path),proof=proof,certificate=str(cert),certificate_sha256=sha256_file(cert)))
    protected=tmp_path/'protected-old';protected.mkdir();(protected/'keep').write_bytes(b'old evidence')
    return entries,tmp_path/'retirement',protected


def test_success_exact_four_preserves_certificates_and_old_evidence(bundle):
    entries,output,old=bundle;r=retire_entries(entries,output,guard=lambda:None)
    assert r['count']==4 and r['bytes']==sum(e['proof']['bytes'] for e in entries)
    assert all(not Path(e['path']).exists() for e in entries)
    assert all(sha256_file(e['certificate'])==e['certificate_sha256'] for e in entries)
    assert (old/'keep').read_bytes()==b'old evidence'
    assert retire_entries(entries,output,guard=lambda:None)==r

@pytest.mark.parametrize('point',['renamed','file_unlinked'])
def test_interruption_preserved_then_explicit_same_plan_resume(bundle,point):
    entries,output,old=bundle
    def fail(at):
        if at==point:raise InterruptedError('fixture')
    with pytest.raises(InterruptedError):retire_entries(entries,output,guard=lambda:None,fault=fail)
    assert (output/'plan.json').exists() and not (output/'report.json').exists()
    assert (old/'keep').exists()
    retire_entries(entries,output,guard=lambda:None)
    assert not list(old.parent.glob('*.retiring-50k'))

@pytest.mark.parametrize('fault',['guard','corrupt','extra','duplicate'])
def test_all_initial_checks_before_any_payload_deletion(bundle,fault):
    entries,output,old=bundle
    def guard():
        if fault=='guard':raise RuntimeError('admission')
    if fault=='corrupt':(Path(entries[-1]['path'])/'payload').write_bytes(b'changed')
    elif fault=='extra':(Path(entries[-1]['path'])/'unbound').write_bytes(b'keep')
    elif fault=='duplicate':entries[-1]=deepcopy(entries[0])
    with pytest.raises((ValueError,RuntimeError)):retire_entries(entries,output,guard=guard)
    assert all(Path(e['path']).exists() for e in entries) and not output.exists()


def test_staging_extra_file_rejects_resume_without_deletion(bundle):
    entries,output,old=bundle
    def fail(at):
        if at=='renamed':raise InterruptedError('fixture')
    with pytest.raises(InterruptedError):retire_entries(entries,output,guard=lambda:None,fault=fail)
    trash=Path(entries[0]['path']+'.retiring-50k');(trash/'unknown').write_text('keep')
    before=file_stats(trash)
    with pytest.raises(ValueError,match='Unknown'):retire_entries(entries,output,guard=lambda:None)
    assert file_stats(trash)==before
