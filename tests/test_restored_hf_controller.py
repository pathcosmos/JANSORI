"""Controller wiring and actual tiny CPU export; production admission mocked."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest
import torch
import torch.distributed.checkpoint as dcp
from tokenizers import Tokenizer,models,pre_tokenizers,decoders,trainers,AddedToken

from common.data_contracts import atomic_json,resolve,sha256_file,identity,artifact_path
from scripts import export_restored_hf as export
from train.final_resume_probe import file_stats,stat_record


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(prefix='test-hf-controller-',dir=resolve('artifacts')) as name:
        yield Path(name)


def simple_contract(root):
    return dict(source=dict(export_root=str(root),main_run=str(root.parent/'fake-main'),native_run=str(root.parent/'fake-native'),
        resume_run=str(root.parent/'fake-resume'),retention_run=str(root.parent/'fake-retention')),
        worker_max_seconds=7200,worker_pss_gib=128,durable_budget_gib=100,additional_local_gib=0,
        cpu_affinity=list(range(32,64)),cpu_threads=16,wait_max_seconds=10,poll_seconds=0,code={},inputs={},env={})


def test_combined_admission_counts_shared_export_once(root,monkeypatch):
    main=root/'fixture-main';main.mkdir();out=root/'output'
    mc=dict(shared_storage={'nfs_pools':{'hf_and_serving':dict(paths=[str(out)],budget_bytes=100*export.GIB)}},max_pss_gib=384,gpu_identity=['fixture']*8)
    atomic_json(main/'contract.json',mc)
    c=simple_contract(out);c['source']['main_run']=str(main)
    hw=dict(gpus=mc['gpu_identity'],memory={'cache_aware_admission':{'headroom_gib':1041}},cpu={'affinity':list(range(96))})
    monkeypatch.setattr('scripts.resource_admission.inspect',lambda:hw)
    calls=[]
    monkeypatch.setattr('scripts.extended_horizon_storage.enforce_shared_space',lambda m,ct:calls.append((m,ct)) or {'shared_pool':1024})
    _,capacity=export.admission(out,c)
    assert capacity['combined_memory_gib']==1040 and capacity['export_additional_local_bytes']==0
    assert len(calls)==1
    hw['memory']['cache_aware_admission']['headroom_gib']=1039
    with pytest.raises(RuntimeError,match='memory'):export.admission(out,c)


def test_dependency_requires_actual_live_matching_process(root):
    script='run_restored_final_resume.py'
    p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)',script,'--run-dir',str(root)])
    try:
        (root/'pid').write_text(str(p.pid));atomic_json(root/'state.json',{'phase':'running'})
        assert export.dependency_ready(root,script) is False
        with pytest.raises(RuntimeError,match='handle'):export.dependency_ready(root,'wrong_controller.py')
    finally:p.terminate();p.wait(timeout=5)
    with pytest.raises(RuntimeError,match='handle'):export.dependency_ready(root,script)
    # A marker triggers the independent source gate; it is not acceptance here.
    atomic_json(root/'.done',{'fixture':True});assert export.dependency_ready(root,script) is True


def test_live_orphan_and_partial_output_refused_without_changes(root):
    p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)',export.SCRIPT,'--run-dir',str(root),'--worker'])
    try:
        with pytest.raises(RuntimeError,match='live'):export.reject_previous(root)
        assert not list(root.iterdir()) and p.poll() is None
    finally:p.terminate();p.wait(timeout=5)
    (root/'hf.copying').mkdir();(root/'hf.copying/evidence').write_bytes(b'preserve')
    with pytest.raises(RuntimeError,match='Preserve'):export.reject_previous(root)
    assert (root/'hf.copying/evidence').read_bytes()==b'preserve'


def test_controller_failure_preserves_source_and_emits_error(root,monkeypatch):
    c=simple_contract(root)
    monkeypatch.setattr(export,'ready',lambda _:dict(ready=True))
    monkeypatch.setattr(export,'admission',lambda *a:({'cpu':{'affinity':list(range(96))}},{}))
    def failed(command,*,root,env,guard):
        (root/'partial-proof').write_bytes(b'preserve')
        raise RuntimeError('deliberate worker failure')
    monkeypatch.setattr('scripts.run_restored_final_resume.supervise_command',failed)
    with pytest.raises(RuntimeError,match='worker failure'):export.controller(root,c)
    assert json.loads((root/'state.json').read_text())['phase']=='error'
    assert not (root/'.done').exists() and (root/'partial-proof').read_bytes()==b'preserve'
    with pytest.raises(RuntimeError,match='Preserve'):export.controller(root,c)


def test_controller_publishes_only_after_independent_verifier(root,monkeypatch):
    c=simple_contract(root);atomic_json(root/'contract.json',c)
    monkeypatch.setattr(export,'ready',lambda _:dict(ready=True))
    monkeypatch.setattr(export,'admission',lambda *a:({'cpu':{'affinity':list(range(96))}},{}))
    monkeypatch.setattr('scripts.run_restored_final_resume.supervise_command',lambda *a,**k:{'exit_code':0})
    seen=[]
    def verify(*a):
        assert not (root/'.done').exists();seen.append('independent')
        return dict(status='fixture_hf_not_production',goal_complete=False)
    monkeypatch.setattr(export,'verify_result',verify)
    report=export.controller(root,c)
    assert seen==['independent'] and report['goal_complete'] is False
    assert json.loads((root/'.done').read_text())['report_sha256']==sha256_file(root/'report.json')


def test_actual_worker_tiny_dcp_to_hf_numeric_and_nfs_manifest(root,monkeypatch):
    from model.model import Transformer,get_config
    from inference.hf_export import TOKENIZER_PROBES
    from scripts.horizon_probe_process import OWNER_ENV
    from inference.verify_package import verify_published
    tok=Tokenizer(models.BPE());tok.pre_tokenizer=pre_tokenizers.ByteLevel(add_prefix_space=False);tok.decoder=decoders.ByteLevel()
    special=[AddedToken(t,special=True,normalized=False) for t in ('<|bos|>','<|eos|>','<|pad|>')]
    tok.train_from_iterator(TOKENIZER_PROBES,trainers.BpeTrainer(vocab_size=280,
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(),special_tokens=special))
    tokenizer=root/'tokenizer-source.json';tok.save(str(tokenizer))
    cfg=get_config('micro',vocab_size=tok.get_vocab_size(),max_seq_len=16);native=Transformer(cfg)
    cp=root/'fixture-checkpoint';cp.mkdir()
    parent=dict(mix={'tokenizer_sha256':sha256_file(tokenizer)})
    meta=dict(step=57000,config=dict(model='micro',vocab_size=cfg.vocab_size,seq_len=16),training_contract=parent)
    dcp.save({'model':native.state_dict()},checkpoint_id=cp);atomic_json(cp/'meta.json',meta);(cp/'.complete').write_text('fixture')
    before=file_stats(cp)
    c=simple_contract(root);c['source'].update(framework=dict(torch=str(torch.__version__),cuda=torch.version.cuda),
        tokenizer=str(tokenizer),prompts='unused-fixture')
    monkeypatch.setattr(export,'load_contract',lambda _:c)
    monkeypatch.setattr(export,'admission',lambda *a:({},{}))
    # Production source is intentionally mocked; everything after DCP reading
    # uses the real native model, HF tokenizer, comparison and NFS publication.
    binding=dict(scope='tiny_fixture_not_final3b',tokenizer_sha256=sha256_file(tokenizer))
    def check():assert file_stats(cp)==before
    def certify(*a,**k):
        atomic_json(root/'source/attestation.json',dict(binding=binding,checkpoint_stats=before,immutable_stats={}))
        return SimpleNamespace(checkpoint=cp,binding=binding,check=check)
    monkeypatch.setattr('inference.restored_final_source.certify_source',certify)
    cases=[dict(id='tiny',category='fixture',ids=[5,6,7],greedy_tokens=4),
           dict(id='full',category='fixture',ids=list(range(3,19)),greedy_tokens=0)]
    monkeypatch.setattr('inference.verify_package.build_cases',lambda *a,**k:cases)
    monkeypatch.setattr(torch,'set_num_interop_threads',lambda n:None)
    ticks=int(Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]);owner='f'*32
    atomic_json(root/'supervision/handle.json',dict(pid=os.getpid(),process={'start_ticks':ticks},owner=owner))
    monkeypatch.setenv(OWNER_ENV,owner);monkeypatch.setenv('CUDA_VISIBLE_DEVICES','')
    old_threads=torch.get_num_threads()
    try:export.worker(root)
    finally:torch.set_num_threads(old_threads)
    assert file_stats(cp)==before
    assert verify_published(root/'hf',source_binding=binding,guard=check)['goal_complete'] is False
    assert json.loads((root/'worker_result.json').read_text())['vllm_verified'] is False
    assert not (root/'.done').exists()  # Outer production reviewer must still run.


def test_guard_limit_rejects_before_controller_done(root,monkeypatch):
    c=simple_contract(root)
    monkeypatch.setattr(export,'ready',lambda _:dict(ready=True))
    monkeypatch.setattr(export,'admission',lambda *a:({'cpu':{'affinity':list(range(96))}},{}))
    def over(command,*,root,env,guard):guard(1234,7201,0)
    monkeypatch.setattr('scripts.run_restored_final_resume.supervise_command',over)
    with pytest.raises(RuntimeError,match='limit'):export.controller(root,c)
    assert not (root/'.done').exists()


def synthetic_review_fixture(root):
    """Synthetic final-shape receipts exercise the outer verifier only."""
    from inference.verify_package import build_cases,payload_manifest
    c=simple_contract(root);s=c['source'];s.update(parent_contract_id='fixture-child',framework={'torch':'fixture','cuda':None},
        tokenizer='tokenizer/candidate_v1/tokenizer.json',prompts='runs/f7_preflight_v1/prompts.jsonl')
    s['main_run']=str(root/'fake-main')
    cp=resolve(s['main_run'])/'training/ckpt_0057000';cp.mkdir(parents=True);(cp/'payload').write_bytes(b'fixture')
    binding=dict(status='verified_actual_final57k_source',checkpoint=str(cp),step=57000,
        training_contract_id=s['parent_contract_id'],export_contract_id=identity(s),committed_tokens=119537664000,
        final_native_verified=True,actual8gpu_restore_verified=True,final_retention_verified=True,
        content=dict(model_tensors=255,state_tensors=1020,rng_ranks=8,payload_files=8,full_payload_sha_refreshed=True),
        tokenizer_sha256=sha256_file(resolve(s['tokenizer'])),quality_success_asserted=False,official_benchmark_measured=False)
    s['expected_source_order']=['fixture_source']
    s['expected_segments']=[dict(root=f'fixture/{name}',contract_id=cid,start_step_exclusive=start,through_step=end)
        for name,cid,start,end in [('old','old',0,8000),('middle','middle',8000,10000),('child','fixture-child',10000,57000)]]
    segments=[dict(contract_id=b['contract_id'],start_step_exclusive=b['start_step_exclusive'],through_step=b['through_step'],
        committed_step_rank_pairs=(b['through_step']-b['start_step_exclusive'])*8,
        committed_tokens=(b['through_step']-b['start_step_exclusive'])*2097152,
        source_tokens={'fixture_source':(b['through_step']-b['start_step_exclusive'])*2097152},
        persisted_attempt_tokens_through_prefix=(b['through_step']-b['start_step_exclusive'])*2097152,
        replayed_tokens_through_prefix=0) for b in s['expected_segments']]
    audit=dict(status='passed_restored_committed_prefix_audit',source_run=s['main_run'],through_step=57000,target_steps=57000,
        validation_source=False,counts=dict(status='passed_ledger_accounting_only',through_step=57000,target_steps=57000,
            segments=segments,source_tokens={'fixture_source':119537664000},persisted_attempt_tokens_through_prefix=119537664000,
            replayed_tokens_through_prefix=0,committed_tokens=119537664000,committed_step_rank_pairs=456000,successful_target_ranks=list(range(8))))
    binding.update(training_audit_id=identity(audit),source_tokens=audit['counts']['source_tokens'],segments=segments,
        replayed_tokens=0,committed_step_rank_pairs=456000,export_contract_id=identity(s))
    atomic_json(root/'source/final_training_audit.json',audit)
    atomic_json(root/'source/attestation.json',dict(binding=binding,checkpoint_stats=file_stats(cp),immutable_stats={}))
    hf=root/'hf';hf.mkdir();(hf/'fixture-payload').write_bytes(b'not-real-3b-weights')
    cases=build_cases(resolve(s['tokenizer']),resolve(s['prompts']),context_length=4096);rows=[]
    for expected in cases:
        r=dict(id=expected['id'],category=expected['category'],positions=len(expected['ids']),full_vocabulary=65536,max_absolute_error=0.)
        if len(expected['ids'])>1:r.update(scored_targets=len(expected['ids'])-1,native_nll=2.,hf_nll=2.,cache_max_absolute_error=0.)
        if expected['greedy_tokens']:r['greedy_ids']=[3]*4
        rows.append(r)
    numeric=dict(status='passed_cpu_fp32_conversion',case_inputs=cases,cases=rows,atol=1e-5,rtol=1e-4,device='cpu',dtype='float32',package_files=payload_manifest(hf))
    atomic_json(hf/'numerical_validation.json',numeric)
    release=dict(status='verified_hf_fp32_runtime_pending',source_binding=binding,
        numerical_validation_sha256=sha256_file(hf/'numerical_validation.json'),files=payload_manifest(hf),
        vllm_verified=False,ollama_verified=False,goal_complete=False,resumable_training_checkpoint=False)
    atomic_json(hf/'release_manifest.json',release)
    command=[sys.executable,'-u',str(resolve(export.SCRIPT)),'--run-dir',str(root),'--worker']
    h=dict(pid=1234,process={'start_ticks':5678},owner='fixture-owner',command=command)
    atomic_json(root/'supervision/handle.json',h)
    atomic_json(root/'supervision/result.json',dict(command=command,exit_code=0,remaining_owned_processes=[],launcher_pid=1234,owner=h['owner']))
    atomic_json(root/'worker_execution.json',dict(pid=1234,start_ticks=5678,owner=h['owner'],contract_id=identity(c),framework=s['framework'],device='cpu'))
    result=dict(status='passed_final57k_hf_export_runtime_pending',source_binding=binding,goal_complete=False,vllm_verified=False,ollama_verified=False)
    for key,path in [('source_attestation_sha256','source/attestation.json'),('worker_execution_sha256','worker_execution.json'),
                     ('publication_manifest_sha256','hf/release_manifest.json'),('numerical_sha256','hf/numerical_validation.json')]:
        result[key]=sha256_file(root/path)
    atomic_json(root/'worker_result.json',result)
    return c,result


def test_independent_review_accepts_bound_fixture_only_and_detects_mutation(root):
    c,result=synthetic_review_fixture(root)
    assert export.verify_result(root,c)==result
    (root/'fake-main/training/ckpt_0057000/payload').write_bytes(b'changed')
    with pytest.raises(ValueError,match='source changed'):export.verify_result(root,c)


def test_independent_review_rejects_supervised_identity_change(root):
    c,_=synthetic_review_fixture(root)
    h=json.loads((root/'supervision/handle.json').read_text());h['owner']='another-owner'
    atomic_json(root/'supervision/handle.json',h)
    with pytest.raises(ValueError,match='execution differs'):export.verify_result(root,c)


def test_independent_review_rejects_numerical_coverage_even_with_rehashed_receipts(root):
    from inference.verify_package import payload_manifest
    c,result=synthetic_review_fixture(root);hf=root/'hf'
    numeric=json.loads((hf/'numerical_validation.json').read_text());numeric['cases'].pop()
    atomic_json(hf/'numerical_validation.json',numeric)
    release=json.loads((hf/'release_manifest.json').read_text())
    release['numerical_validation_sha256']=sha256_file(hf/'numerical_validation.json')
    release['files']=payload_manifest(hf,exclude=('release_manifest.json',))
    atomic_json(hf/'release_manifest.json',release)
    result['numerical_sha256']=sha256_file(hf/'numerical_validation.json')
    result['publication_manifest_sha256']=sha256_file(hf/'release_manifest.json')
    atomic_json(root/'worker_result.json',result)
    with pytest.raises(ValueError,match='coverage'):export.verify_result(root,c)


def test_registry_spec_requires_review_and_content_proofs(root):
    spec=export.registration(root)
    assert spec['requires_review'] is True and spec['pid_cmd_contains']==export.SCRIPT
    assert spec['certificate']==artifact_path(root/'.done')
    assert {r['path'] for r in spec['proofs']}=={artifact_path(root/'report.json'),artifact_path(root/'contract.json')}


def test_old_horizon_target_rejected_even_with_rehashed_preparation(root):
    c=simple_contract(root);c['purpose']='restored_hf_export_controller';c['source']['target_steps']=35000
    atomic_json(root/'contract.json',c);atomic_json(root/'prepared.json',dict(contract_sha256=sha256_file(root/'contract.json')))
    with pytest.raises(ValueError,match='Unreviewed'):export.load_contract(root)


def test_exact_export_reservation_required_before_output_creation(root,monkeypatch):
    main=root/'fixture-main';main.mkdir();out=root/'output';c=simple_contract(out);c['source']['main_run']=str(main)
    mc=dict(shared_storage={'nfs_pools':{'hf_and_serving':dict(paths=[str(out)],budget_bytes=16*2**20)}})
    atomic_json(main/'contract.json',mc)
    with pytest.raises(ValueError,match='reserved'):export.admission(out,c)
    assert not out.exists()


def test_publication_rejects_relabelled_original_ledger(root):
    c,_=synthetic_review_fixture(root)
    p=root/'source/final_training_audit.json';audit=json.loads(p.read_text())
    audit['counts']['segments'][0]['contract_id']='fixture-child';atomic_json(p,audit)
    with pytest.raises(ValueError,match='identity'):export.verify_result(root,c)
