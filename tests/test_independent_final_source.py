"""Explicit final-source protocol; actual GPU runtime acceptance is mocked."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
from common.data_contracts import resolve,sha256_file,artifact_path,identity
from scripts.tiered_checkpoint_retention import atomic_json
from scripts import independent_final_source as m
from scripts import evaluate_restored_native as native
from test_independent_training_audit import case


@pytest.fixture
def view(tmp_path,monkeypatch):
    main=tmp_path/'main';main.mkdir();(main/'training').mkdir()
    cfg=dict(max_steps=57000,global_tokens=2097152,log_every=10,eval_every=250,ckpt_every=500)
    observed=dict(phase='running',completion=None,processes=dict(supervisor={'pid':1},launcher={'pid':2},all8_consumed_live=True),consumption={'proof':True})
    value=SimpleNamespace(main=main,config=cfg,observe=lambda:deepcopy(observed),observed=observed)
    monkeypatch.setattr(m,'source_view',lambda *a:value)
    return value


def write_step(view,step):
    (view.main/'training/log.jsonl').write_text(json.dumps(dict(step=step,loss=2.,gnorm=.1,lr=.0003,tok_s=137000.,tokens_b=step*2097152/1e9))+'\n')


@pytest.mark.parametrize('step',[56000,56500,57000])
def test_prewarm_trigger_and_explicit_native_route(view,step):
    write_step(view,step)
    if step==57000:view.observed['completion']={'producer_proof':True}
    result=native.main_observation(view.main,{'explicit':'fixture'})
    assert result['ready']==(step>=56500) and result['complete']==(step==57000)


@pytest.mark.parametrize('fault',['error','launcher','rank','consumption','waiting','false_final'])
def test_invalid_or_pending_runtime_never_starts_final_evaluation(view,fault):
    write_step(view,56500)
    if fault=='error':view.observed['phase']='error';view.observed['completion']={'fake':True}
    if fault=='launcher':view.observed['processes']['launcher']=None
    if fault=='rank':view.observed['processes']['all8_consumed_live']=False
    if fault=='consumption':view.observed['consumption']=None
    if fault=='waiting':view.observed['phase']='await_independent_execution'
    if fault=='false_final':view.observed['completion']={'fake':True}
    if fault in ('consumption','waiting'):assert not m.observation(view.main,{})['ready']
    else:
        with pytest.raises((ValueError,RuntimeError)):m.observation(view.main,{})


def test_reference_is_explicit_and_hash_bound_before_any_runtime_cache(tmp_path,monkeypatch):
    control=tmp_path/'control';control.mkdir();atomic_json(control/'runtime_contract.json',{'synthetic':True})
    monkeypatch.setattr(m,'cached_view',lambda *a:pytest.fail('must reject before cached admission'))
    for reference in [None,{},dict(control_dir=str(control),runtime_contract_sha256='0'*64)]:
        with pytest.raises(ValueError):m.source_view(tmp_path,reference)


def test_native_explicit_runtime_does_not_fall_back_to_old_parent_auditor(tmp_path,monkeypatch):
    calls=[]
    def independent(*args):calls.append(args);raise ValueError('missing actual runtime acceptance')
    monkeypatch.setattr(m,'completed_source',independent)
    monkeypatch.setattr('scripts.audit_restored_training.audit_run',lambda *a:pytest.fail('forbidden parent fallback'))
    with pytest.raises(ValueError,match='actual runtime'):native.completed_main(tmp_path,{'explicit':'runtime'})
    assert calls==[(tmp_path,{'explicit':'runtime'})]


def test_native_contract_pins_explicit_source_without_parent_admission(tmp_path,monkeypatch):
    main=tmp_path/'main';main.mkdir();ref=tmp_path/'reference';ref.mkdir()
    tokenizer=tmp_path/'tokenizer.json';tokenizer.write_text('{}');auth=tmp_path/'auth.json';auth.write_text('{}')
    atomic_json(main/'main.json',dict(model='3b',max_steps=57000))
    c=dict(configurations={'main.json':sha256_file(main/'main.json')},code={},evidence={},tokenizer=str(tokenizer),framework={},retention_authorization=str(auth),retention_revision={'authorization':str(auth)})
    atomic_json(main/'contract.json',c)
    result=dict(contract={'tokenizer_sha256':sha256_file(tokenizer)},languages={k:{'valid_tokens':40} for k in ['ko','en']});atomic_json(ref/'result.json',result)
    report=dict(result='result.json',result_sha256=sha256_file(ref/'result.json'));rc=dict(code={},inputs={},evaluation={},samples='synthetic',holdout_manifest='synthetic')
    for n,v in [('report.json',report),('contract.json',rc),('.done',{})]:atomic_json(ref/n,v)
    runtime=tmp_path/'runtime.json';runtime.write_text('{}');reference=dict(control_dir=str(tmp_path),runtime_contract_sha256=sha256_file(runtime))
    monkeypatch.setattr('scripts.supervise_extended_training.require_authorization',lambda *a:None)
    monkeypatch.setattr('train.extended_handoff.validate_configuration',lambda *a:None)
    monkeypatch.setattr('train.extended_handoff.load_handoff',lambda *a:pytest.fail('must not read protected parents'))
    monkeypatch.setattr(native,'certified',lambda *a:(report,rc))
    monkeypatch.setattr(m,'bind_source',lambda *a:dict(reference=reference,code={m.CODE[0]:sha256_file(resolve(m.CODE[0]))},inputs={str(runtime):sha256_file(runtime)}))
    produced=native.make_contract(main,ref,runtime_control=tmp_path)
    assert produced['independent_runtime']==reference and produced['inputs'][str(runtime)]==sha256_file(runtime)
    assert produced['final_step']==57000 and produced['late_start_step']==56500 and produced['durable_budget_gib']==8


def test_full57000_source_recomputes_real_prefix_and_synthetic_suffix_without_parent(case,monkeypatch):
    """Real sealed9500 prefix, synthetic47500step state/ledgers; GPU admission mocked."""
    from train.train import lr_at
    c=case;source=c.candidate['source_order'][0];target=57000
    gens=[dict(id=p['id'],prompt=p['text'],category=p['category'],generated_ids=[1]) for p in c.prompts]
    generations=[dict(step=s,sha256=identity(gens)) for s in c.contract['generation_steps'] if 9500<s<=target]
    for rank in range(8):
        c.write(c.root/'ledgers'/f'fixture-rank{rank}.json',dict(rank=rank,success=True,contract_id=c.candidate['id'],optimizer_initializations=1,
            steps=[dict(step=s,source_sequences={source:64}) for s in range(9501,target+1)],generations=generations))
    rows=[dict(step=s,loss=2.,gnorm=.2,lr=lr_at(s-1,c.config),tok_s=140000.,tokens_b=s*2097152/1e9) for s in range(9510,target+1,10)]
    for step in range(9750,target+1,250):
        cats=Counter();by_source={}
        for g in c.probe['groups']:
            n=g['count']*4096;cats[g['category']]+=n;by_source[g['source']]=dict(valid_tokens=n,nll_sum=n*2.,holdout_loss=2.)
        row=dict(step=step,contract_id=c.candidate['id'],valid_tokens=c.probe['target_tokens'],nll_sum=c.probe['target_tokens']*2.,holdout_loss=2.,by_source=by_source,
            by_category={k:dict(valid_tokens=n,nll_sum=n*2.,holdout_loss=2.) for k,n in cats.items()})
        if step in c.contract['generation_steps']:row['fixed_generations']=gens
        rows.append(row)
    (c.root/'training/log.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in sorted(rows,key=lambda r:r['step'])))
    cp=c.cp.with_name('ckpt_0057000');c.cp.rename(cp);meta=json.loads((cp/'meta.json').read_text());meta['step']=target;c.write(cp/'meta.json',meta)
    files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(cp.iterdir())]
    cert=c.root/'checkpoint_certificates/ckpt_0057000.json';c.write(cert,dict(status='passed_main_checkpoint_storage',checkpoint=artifact_path(cp),step=target,training_contract_id=c.candidate['id'],files=files,bytes=sum(r['bytes'] for r in files)))
    c.revision['child']=dict(step=target,checkpoint=str(cp),certificate_sha256=sha256_file(cert));c.write(c.revision_path,c.revision)
    c.write(c.root/'report.json',dict(status='main_target_completed_review_pending',final_checkpoint=artifact_path(cp),trained_tokens=119537664000))
    c.write(c.root/'.done',dict(report_sha256=sha256_file(c.root/'report.json'),contract_sha256=sha256_file(c.root/'contract.json')))
    runtime=c.root/'synthetic_runtime.json';atomic_json(runtime,{'not_actual_gpu':True})
    view=SimpleNamespace(main=c.root,path=runtime,digest=sha256_file(runtime),acceptance={'parent_training_contract_id':c.candidate['id']},
        observe=lambda:dict(completion={'certificate_sha256':sha256_file(cert)}),unchanged=lambda:None)
    monkeypatch.setattr(m,'source_view',lambda *a:view);monkeypatch.setattr(m,'observation',lambda *a:{'complete':True})
    monkeypatch.setattr(m,'final_revision',lambda *a:c.revision_path)
    shutil.rmtree(c.parent)
    checkpoint,proof,meta,audit=native.completed_main(c.root,{'explicit_gpu_acceptance_mocked':True})
    assert checkpoint==cp and audit['counts']['committed_tokens']==119537664000 and audit['counts']['committed_step_rank_pairs']==456000
    assert audit['counts']['successful_target_ranks']==list(range(8)) and len(audit['counts']['segments'])==3
    assert not audit['parent_payload_read'] and not audit['goal_complete'] and not audit['actual_final_gpu_restore']
    path=c.root/'ledgers/fixture-rank7.json';value=json.loads(path.read_text());value['success']=False;c.write(path,value)
    with pytest.raises(ValueError,match='successful'):native.completed_main(c.root,{'explicit_gpu_acceptance_mocked':True})
