"""Final source admission: real file bindings; production GPU/content gates mocked.

The existing running source cannot pass final publication admission.
Synthetic57000 receipts in other cases never count as actual final training.
"""
import copy
import json
from pathlib import Path
import tempfile

import pytest
from tokenizers import Tokenizer, models

from common.data_contracts import atomic_json,sha256_file,artifact_path,resolve,identity
from inference.restored_final_source import make_contract,certify_source,require_final_identity
from train.final_resume_probe import file_stats,stat_record


@pytest.fixture
def env(monkeypatch):
    base_root = resolve('artifacts');base_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='test-export-admission-',dir=base_root) as folder:
        b=Path(folder); main=b/'main'; native=b/'native'; resume=b/'resume'; retention=b/'retention'; root=b/'export'
        for p in (main,native,resume,retention,root):p.mkdir()
        tok=b/'tokenizer.json';Tokenizer(models.WordLevel({'<|bos|>':0,'<|eos|>':1,'<|pad|>':2,'x':3},unk_token='x')).save(str(tok))
        tokens=b/'tokens.bin';tokens.write_bytes(b'actual-tiny-input-bytes')
        prompts=b/'prompts.jsonl';prompts.write_text('{}\n')
        cfg=dict(model='3b',max_steps=57000,global_tokens=2097152,out_dir=str(main/'training'))
        parent=dict(world_size=8,config=cfg,source_order=['fixture_source'],mix={'tokenizer_sha256':sha256_file(tok)},
                    files={str(p):sha256_file(p) for p in (tok,tokens)})
        parent['id']=identity(parent)
        atomic_json(main/'main.json',cfg);atomic_json(main/'training/training_contract.json',parent)
        previous=[b/'old',b/'middle'];segments=[]
        for path,cid,start,end in [(previous[0],'original',0,8000),(previous[1],'middle',8000,10000),(main,parent['id'],10000,57000)]:
            if path!=main:atomic_json(path/'training/training_contract.json',dict(id=cid,source_order=parent['source_order']))
            segments.append(dict(root=artifact_path(path),contract_id=cid,start_step_exclusive=start,through_step=end))
        mc=dict(retention_branches=[{'run':artifact_path(p)} for p in [*previous,main]],
            retention_authorization=artifact_path(main/'authorization.json'),retention_revision={'authorization':artifact_path(main/'authorization.json')},configurations={'main.json':sha256_file(main/'main.json')},code={},evidence={},
            shared_storage={'nfs_pools':{key:dict(paths=[str(p)],budget_bytes=gib*2**30) for key,p,gib in [('hf_and_serving',root,100),('final_native',native,8),('final_resume',resume,1),('final_retention',retention,1)]}},
            tokenizer=str(tok),prompts=str(prompts),framework={'torch':'fixture','cuda':None})
        atomic_json(main/'contract.json',mc);atomic_json(main/'authorization.json',{'fixture':True})
        plan=dict(main_run=artifact_path(main),native_run=artifact_path(native),parent_step=57000)
        atomic_json(resume/'plan.json',plan);atomic_json(resume/'prepared.json',{'fixture':True})
        reference=b/'reference.json';atomic_json(reference,{'fixture':'native_reference'})
        nc=dict(purpose='final_native_main_evaluation',main_run=artifact_path(main),final_step=57000,
                code={},inputs={},reference_report=str(reference),expected_targets={'ko':1,'en':1},tokenizer_sha256=sha256_file(tok))
        rc=dict(purpose='restored_final_resume_controller',main_run=artifact_path(main),code={},inputs={})
        tc=dict(expected_segments=segments,expected_source_order=parent['source_order'],purpose='restored_final_retention_audit_controller',main_run=artifact_path(main),native_run=artifact_path(native),
                resume_run=artifact_path(resume),code={},inputs={})
        for p,c in [(native,nc),(resume,rc),(retention,tc)]:atomic_json(p/'contract.json',c)
        monkeypatch.setattr('scripts.supervise_extended_training.require_authorization',lambda c:None)
        monkeypatch.setattr('train.extended_handoff.validate_configuration',lambda c,m:None)
        monkeypatch.setattr('train.extended_handoff.load_handoff',lambda *a:({},parent,{}))
        monkeypatch.setattr('scripts.run_restored_final_resume.load_plan',lambda p:plan)
        cp=main/'training/ckpt_0057000';cp.mkdir()
        meta=dict(step=57000,world_size=8,training_contract=parent,config=cfg)
        atomic_json(cp/'meta.json',meta);(cp/'payload').write_bytes(b'fixture-state')
        proof=dict(status='passed_main_checkpoint_storage',step=57000,training_contract_id=parent['id'],checkpoint=artifact_path(cp))
        cert=main/'checkpoint_certificates/ckpt_0057000.json';atomic_json(cert,proof)
        committed_segments=[dict(contract_id=s['contract_id'],start_step_exclusive=s['start_step_exclusive'],through_step=s['through_step'],
            committed_step_rank_pairs=(s['through_step']-s['start_step_exclusive'])*8,
            committed_tokens=(s['through_step']-s['start_step_exclusive'])*2097152,
            source_tokens={'fixture_source':(s['through_step']-s['start_step_exclusive'])*2097152},
            persisted_attempt_tokens_through_prefix=(s['through_step']-s['start_step_exclusive'])*2097152,
            replayed_tokens_through_prefix=0) for s in segments]
        audit=dict(status='passed_restored_committed_prefix_audit',source_run=artifact_path(main),validation_source=False,through_step=57000,target_steps=57000,
                   counts=dict(status='passed_ledger_accounting_only',target_steps=57000,through_step=57000,segments=committed_segments,source_tokens={'fixture_source':119537664000},persisted_attempt_tokens_through_prefix=119537664000,replayed_tokens_through_prefix=0,committed_tokens=119537664000,committed_step_rank_pairs=456000,
                     successful_target_ranks=list(range(8)),child_contract_id=parent['id']),
                   checkpoint_certificate=artifact_path(cert),evidence={artifact_path(cert):sha256_file(cert)})
        monkeypatch.setattr('scripts.evaluate_restored_native.completed_main',lambda p:(cp,proof,meta,audit))
        def content(path, proof, parent, cfg, *, snapshot, hashes, production):
            assert production and hashes
            snapshot.checkpoints[str(path)]=file_stats(path)
            return dict(model_tensors=255,state_tensors=1020,rng_ranks=8,payload_files=8,full_payload_sha_refreshed=True)
        monkeypatch.setattr('scripts.audit_checkpoint_retention.checkpoint_content',content)
        # Actual certified() still reads and hashes these JSONs/.done markers.
        atomic_json(native/'3b_final.json',{'fixture':'native_result'})
        nr=dict(status='passed_actual_final3b_native_evaluation',result='3b_final.json',result_sha256=sha256_file(native/'3b_final.json'))
        atomic_json(native/'report.json',nr)
        for p in (main,resume,retention):atomic_json(p/'report.json',{'fixture':str(p)})
        for p in (main,native,resume,retention):
            atomic_json(p/'.done',dict(report_sha256=sha256_file(p/'report.json'),contract_sha256=sha256_file(p/'contract.json')))
        calls=[]
        monkeypatch.setattr('scripts.evaluate_final_main.verify_result',lambda *a,**k:calls.append('native'))
        diag=resume/'proof.json';atomic_json(diag,{'scope':'synthetic8rank'})
        rr=dict(status='passed_actual_final3b_8gpu_resume',parent_step=57000,target_steps=57000,main_target_modified=False,
                parent_checkpoint=artifact_path(cp),evidence={artifact_path(diag):sha256_file(diag)})
        tr=dict(through_step=57000,nfs_resume_candidates=['ckpt_0057000'],metadata_evidence={},training_audit={'evidence':{}})
        monkeypatch.setattr('scripts.run_restored_final_resume.verify_completed',lambda *a:rr)
        monkeypatch.setattr('scripts.run_restored_retention_audit.verify_completed',lambda *a:tr)
        def fresh(p,out):
            for name,digest in parent['files'].items():assert sha256_file(name)==digest
            atomic_json(out,{'scope':'actual_tiny_sha_not_production_input_contract'});calls.append('fresh_inputs')
            return {name:stat_record(Path(name)) for name in parent['files']}
        monkeypatch.setattr('scripts.run_restored_final_resume.fresh_inputs',fresh)
        c=make_contract(main,root,native,resume,retention)
        yield dict(b=b,main=main,root=root,native=native,resume=resume,retention=retention,tok=tok,tokens=tokens,cfg=cfg,mc=mc,parent=parent,
                   cp=cp,proof=proof,meta=meta,audit=audit,c=c,rr=rr,tr=tr,calls=calls,nc=nc,diag=diag)


def attest(e,guard=lambda:None):
    return certify_source(e['c'],e['root']/'source',guard=guard)


def test_full_dependency_wiring_and_live_immutable_guard(env):
    e=env;before=file_stats(e['main']);live=attest(e);live.check()
    assert e['calls']==['native','fresh_inputs']
    assert live.binding['actual8gpu_restore_verified'] and live.binding['final_retention_verified']
    assert live.binding['goal_complete'] is False and live.binding['quality_success_asserted'] is False
    assert before==file_stats(e['main'])
    assert (e['root']/'source/attestation.json').is_file()
    with pytest.raises(ValueError,match='new exact'):attest(e)


@pytest.mark.parametrize('mutation',['step','tokens','rank','local','contract'])
def test_wrong_final_identity_refused_before_export(env,mutation):
    e=env;audit=copy.deepcopy(e['audit']);proof=copy.deepcopy(e['proof']);meta=copy.deepcopy(e['meta']);cp=e['cp']
    if mutation=='step':meta['step']=34000
    if mutation=='tokens':audit['counts']['committed_tokens']-=2097152
    if mutation=='rank':audit['counts']['successful_target_ranks'].pop()
    if mutation=='local':cp=e['b']/'local/ckpt_0057000'
    if mutation=='contract':proof['training_contract_id']='wrong'
    with pytest.raises(ValueError,match='identity'):
        require_final_identity(cp,proof,meta,audit,e['c'],e['parent'])


@pytest.mark.parametrize('what',['checkpoint','input','evidence','new_checkpoint_file'])
def test_post_hash_mutation_rejected(env,what):
    e=env;live=attest(e)
    path={'checkpoint':e['cp']/'payload','input':e['tokens'],'evidence':e['diag'],'new_checkpoint_file':e['cp']/'unexpected'}[what]
    path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'):live.check()


def test_source_guard_capacity_failure_prevents_evidence_namespace(env):
    def guard():raise RuntimeError('combined capacity unavailable')
    with pytest.raises(RuntimeError,match='capacity'):attest(env,guard)
    assert not (env['root']/'source').exists()


def test_verified_receipt_digest_cannot_be_replaced_before_binding(env,monkeypatch):
    e=env
    def changed(*a):
        e['diag'].write_text('different proof');return e['rr']
    monkeypatch.setattr('scripts.run_restored_final_resume.verify_completed',changed)
    with pytest.raises(ValueError,match='verified diagnostic digest'):attest(e)
    assert not (e['root']/'source/attestation.json').exists()


def test_final_report_changed_during_dependency_check_refused(env,monkeypatch):
    e=env
    def changed(*a):
        atomic_json(e['native']/'report.json',{'changed':True});return e['rr']
    monkeypatch.setattr('scripts.run_restored_final_resume.verify_completed',changed)
    with pytest.raises(ValueError,match='metadata changed'):attest(e)


def test_target_reservation_and_dependency_identity_admission(env):
    e=env;make=lambda:make_contract(e['main'],e['root'],e['native'],e['resume'],e['retention'])
    e['mc']['shared_storage']['nfs_pools']['hf_and_serving']['budget_bytes']=99*2**30;atomic_json(e['main']/'contract.json',e['mc'])
    with pytest.raises(ValueError,match='reservation'):make()
    e['mc']['shared_storage']['nfs_pools']['hf_and_serving']['budget_bytes']=100*2**30;atomic_json(e['main']/'contract.json',e['mc'])
    e['nc']['final_step']=35000;atomic_json(e['native']/'contract.json',e['nc'])
    with pytest.raises(ValueError,match='dependency target'):make()


def test_running_production_cannot_publish_with_deferred_budget():
    main=resolve('runs/3b_full_57k_v2');before={n:sha256_file(main/n) for n in ('main.json','contract.json')}
    with pytest.raises(ValueError,match='reservation'):
        make_contract(main,resolve('artifacts/3b_base_57k_v2'),resolve('runs/3b_57k_final_native_v2'),
                      resolve('runs/3b_57k_final_resume_v2'),resolve('runs/3b_57k_final_retention_v2'))
    assert before=={n:sha256_file(main/n) for n in before}


def test_new_export_hash_cannot_mask_stale_main_binding(env):
    e=env;e['mc']['code']['inference/hf_export.py']='0'*64
    atomic_json(e['main']/'contract.json',e['mc'])
    with pytest.raises(ValueError,match='Bound main code'):
        make_contract(e['main'],e['root'],e['native'],e['resume'],e['retention'])

@pytest.mark.parametrize('mutation',['owner','boundary','source','replay','status'])
def test_three_segments_cannot_be_relabelled_or_count_replays_as_progress(env,mutation):
    e=env;audit=copy.deepcopy(e['audit']);counts=audit['counts']
    if mutation=='owner':counts['segments'][0]['contract_id']=e['parent']['id']
    if mutation=='boundary':counts['segments'][1]['start_step_exclusive']=7999
    if mutation=='source':counts['segments'][0]['source_tokens']['fixture_source']+=1
    if mutation=='replay':counts['replayed_tokens_through_prefix']=2097152
    if mutation=='status':audit['status']='passed_35k'
    with pytest.raises(ValueError):require_final_identity(e['cp'],e['proof'],e['meta'],audit,e['c'],e['parent'])


def test_consistent_reexecution_is_separate_from_committed_total(env):
    e=env;audit=copy.deepcopy(e['audit']);counts=audit['counts'];n=2097152
    counts['segments'][2]['replayed_tokens_through_prefix']+=n
    counts['segments'][2]['persisted_attempt_tokens_through_prefix']+=n
    counts['replayed_tokens_through_prefix']+=n;counts['persisted_attempt_tokens_through_prefix']+=n
    require_final_identity(e['cp'],e['proof'],e['meta'],audit,e['c'],e['parent'])
