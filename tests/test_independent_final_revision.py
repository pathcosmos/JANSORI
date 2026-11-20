"""Final-source file protocol using real NFS fixtures, not actual GPU acceptance."""
import fcntl
import json
from types import SimpleNamespace

import pytest
from common.data_contracts import sha256_file
from scripts.tiered_checkpoint_retention import atomic_json
from scripts import independent_final_source as m
from test_latest_independent_revision import case,initialize,add_checkpoint


def setup_view(c,monkeypatch):
    pool=initialize(c,monkeypatch);control=pool/'control';control.mkdir()
    cp,cert=add_checkpoint(c,57000)
    view=SimpleNamespace(main=c.root,control=control,
        binding=dict(revision_template=dict(path=str(c.review_path),sha256=sha256_file(c.review_path)),
                     main_contract_sha256=sha256_file(c.root/'contract.json')),
        observe=lambda:{'completion':{'fixture_only':True}})
    return view,cp,cert


def test_final_revision_actual_prepare_and_reuse_without_parent(case,monkeypatch):
    import shutil
    view,cp,cert=setup_view(case,monkeypatch)
    shutil.rmtree(case.parent)
    revision=m.final_revision(view);before=revision.read_bytes()
    assert revision==view.control/'final_source_v1/revision.json'
    assert json.loads(before)['child']['step']==57000
    monkeypatch.setattr(m,'prepare',lambda *a,**kw:pytest.fail('published revision must be reused'))
    assert m.final_revision(view)==revision and revision.read_bytes()==before
    assert not json.loads((revision.parent/'prepared.json').read_text())['production_adoption_authorized']


@pytest.mark.parametrize('fault',['not_complete','not_final','partial','locked','wrong_template','wrong_main','tampered_marker','changed_cert'])
def test_final_revision_rejects_unsafe_publication_or_reuse(case,monkeypatch,fault):
    view,cp,cert=setup_view(case,monkeypatch)
    directory=view.control/'final_source_v1'
    if fault=='not_complete':view.observe=lambda:{'completion':None}
    elif fault=='not_final':
        import shutil
        shutil.rmtree(cp);cert.unlink()
    elif fault=='partial':directory.mkdir();(directory/'residue').write_text('preserve failure')
    elif fault=='locked':
        with (view.control/'final_source.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with pytest.raises(BlockingIOError):m.final_revision(view)
        assert not directory.exists();return
    else:
        revision=m.final_revision(view)
        if fault=='wrong_template':view.binding['revision_template']['sha256']='0'*64
        elif fault=='wrong_main':view.binding['main_contract_sha256']='0'*64
        elif fault=='tampered_marker':atomic_json(directory/'prepared.json',{})
        elif fault=='changed_cert':
            value=json.loads(cert.read_text());value['extra_review']='new certificate bytes';atomic_json(cert,value)
    with pytest.raises((ValueError,OSError)):m.final_revision(view)
    if fault=='partial':assert (directory/'residue').read_text()=='preserve failure'
    if fault in ('not_complete','not_final'):assert not directory.exists()


@pytest.mark.parametrize('fault',['wrong_main','changed_pin','cache_changed','nonstring_digest','nonstring_control'])
def test_source_view_checks_reference_and_immutable_cache(tmp_path,monkeypatch,fault):
    control=tmp_path/'control';control.mkdir();main=tmp_path/'main';main.mkdir()
    atomic_json(control/'runtime_contract.json',{'fixture_only':True})
    ref=dict(control_dir=str(control),runtime_contract_sha256=sha256_file(control/'runtime_contract.json'))
    calls=[]
    def unchanged():
        calls.append(True)
        if fault=='cache_changed':raise ValueError('immutable source evidence changed')
    view=SimpleNamespace(main=main,unchanged=unchanged)
    monkeypatch.setattr(m,'cached_view',lambda *a:view)
    if fault=='wrong_main':view.main=tmp_path/'different'
    elif fault=='changed_pin':ref['runtime_contract_sha256']='0'*64
    elif fault=='nonstring_digest':ref['runtime_contract_sha256']=None
    elif fault=='nonstring_control':ref['control_dir']=None
    with pytest.raises(ValueError):m.source_view(main,ref)
    assert calls==([True] if fault=='cache_changed' else [])


@pytest.mark.parametrize('fault',[None,'owner','acceptance'])
def test_original_candidate_is_recomputed_without_parent_payload(case,monkeypatch,fault):
    import shutil
    c=case;initialize(c,monkeypatch);shutil.rmtree(c.parent)
    view=SimpleNamespace(main=c.root,acceptance={'parent_training_contract_id':c.candidate['id']},unchanged=lambda:None)
    monkeypatch.setattr(m,'source_view',lambda *a:view)
    if fault=='owner':
        owner=json.loads((c.root/'training/training_contract.json').read_text());owner['world_size']=7
        atomic_json(c.root/'training/training_contract.json',owner)
    elif fault=='acceptance':view.acceptance['parent_training_contract_id']='wrong'
    if fault:
        with pytest.raises(ValueError,match='numerical identity'):m.admitted_candidate(c.root,{})
    else:assert m.admitted_candidate(c.root,{})==c.candidate
