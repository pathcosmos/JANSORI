"""Synthetic metadata identities; no payload, training or GPU operations."""
from copy import deepcopy
from pathlib import Path
import pytest

from common.data_contracts import identity
from scripts.budget_final_lineage import intervals
from scripts.budget_completed_identity import reconstruct_identity, verify_completed_metadata
from train.budget_horizon_contract import BRIDGE_CODE, bound_candidate
from test_budget_horizon import fixture as lineage_fixture


@pytest.fixture(scope='module')
def parent_metadata():
    return lineage_fixture()['parent_metadata']


def documents(tmp_path, parent_metadata):
    metadata = deepcopy(parent_metadata)
    source, main = tmp_path/'restored', tmp_path/'budget'
    files = [dict(path=n, bytes=1, sha256='a'*64) for n in
             ['.complete', '.metadata', 'meta.json', 'state.distcp',
              *[f'rng_rank{r}.pt' for r in range(8)]]]
    proof = dict(status='passed_main_checkpoint_storage', step=16000,
        training_contract_id=metadata['training_contract']['id'], files=files,
        bytes=len(files), checkpoint=str(source/'training/ckpt_0016000'))
    binding = dict(parent_contract_id=metadata['training_contract']['id'], parent_step=16000,
        parent_metadata_id=identity(metadata), parent_certificate_sha256='b'*64,
        parent_certificate_id=identity(proof), parent_ledger_manifest_sha256='c'*64,
        code={p:'d'*64 for p in BRIDGE_CODE})
    actual = bound_candidate(metadata, binding)
    config = dict(actual['config'], model='3b', vocab_size=65536, micro_bsz=4,
        compile=False, eval_every=250, ckpt_every=500, save_final=True, log_every=10,
        keep_ckpts=50001, stop_after=None, out_dir=str(main/'training'))
    outer = dict(purpose='main_training', main_training_authorized=True,
        new_checkpoint_pruning_authorized=True, main_run=str(main), target_steps=50000,
        budget_execution=binding, code=dict(binding['code']),
        generation_steps=actual['monitored_evaluation']['generation_steps'],
        retention_branches=[dict(run=str(p), first_step=a, last_step=b)
            for p,(a,b) in zip([tmp_path/'original', tmp_path/'short', source, main],
                               intervals(16000, checkpoints=True))])
    receipt = dict(status='sealed_budget_parent_handoff', execution_binding=deepcopy(binding),
        parent_metadata=metadata, candidate_contract_id=actual['id'], protection_root=str(main),
        source_run=str(source), parent_checkpoint=str(main/'parent_checkpoint/ckpt_0016000'))
    return outer, config, receipt, proof, actual


def test_reconstructs_identical_contract_without_opening_ancestors(tmp_path, parent_metadata, monkeypatch):
    args = documents(tmp_path, parent_metadata)
    before = deepcopy(args)
    def forbidden(*args, **kwargs): raise AssertionError('Unexpected file read')
    monkeypatch.setattr(Path, 'open', forbidden)
    assert reconstruct_identity(*args, parent_certificate_sha256='b'*64) == args[-1]
    assert args == before


@pytest.mark.parametrize('fault', ['old_target','wrong_receipt','byte_sha','canonical_proof',
    'binding_step','candidate_id','rehash_change','code','generation','old_owners',
    'source','protected','original','output','parent_rank','unauthorized'])
def test_rejects_identity_or_scope_substitution(tmp_path, parent_metadata, fault):
    c,cfg,r,p,a = documents(tmp_path, parent_metadata)
    digest = 'b'*64
    if fault=='old_target': c['target_steps']=57000
    elif fault=='wrong_receipt': r['status']='historical57k_handoff'
    elif fault=='byte_sha': digest='e'*64
    elif fault=='canonical_proof': p['files'][0]['sha256']='e'*64
    elif fault=='binding_step': r['execution_binding']['parent_step']=16500
    elif fault=='candidate_id': r['candidate_contract_id']='e'*64
    elif fault=='rehash_change':
        a['config']['lr']=.0002; a['id']=identity({k:v for k,v in a.items() if k!='id'})
    elif fault=='code': c['code']['train/budget_parent_resume.py']='e'*64
    elif fault=='generation': c['generation_steps']=[50000]
    elif fault=='old_owners': c['retention_branches'].pop(0)
    elif fault=='source': r['source_run']=str(tmp_path/'short')
    elif fault=='protected': r['parent_checkpoint']=p['checkpoint']
    elif fault=='original': p['checkpoint']=r['parent_checkpoint']
    elif fault=='output': cfg['out_dir']=str(tmp_path/'different')
    elif fault=='parent_rank': r['parent_metadata']['world_size']=4
    elif fault=='unauthorized': c['main_training_authorized']=False
    with pytest.raises(ValueError): reconstruct_identity(c,cfg,r,p,a,parent_certificate_sha256=digest)


def completed(tmp_path, parent_metadata):
    c,cfg,r,p,a=documents(tmp_path,parent_metadata)
    own=str(Path(c['main_run'])/'training/ckpt_0050000')
    meta=dict(step=50000,training_contract=deepcopy(a),config=deepcopy(cfg),world_size=8,
              tokens_per_step=2097152,sampler_version=2)
    cert=dict(deepcopy(p),step=50000,checkpoint=own,training_contract_id=a['id'])
    report=dict(status='main_target_completed_review_pending',trained_tokens=104857600000,final_checkpoint=own)
    marker=dict(report_sha256='a'*64,contract_sha256='b'*64)
    return c,cfg,a,meta,cert,report,marker


def test_final_metadata_is_not_ledger_restore_or_retirement_acceptance(tmp_path,parent_metadata):
    result=verify_completed_metadata(*completed(tmp_path,parent_metadata),
        report_sha256='a'*64,contract_sha256='b'*64)
    assert result['target_step']==50000
    assert not any(result[k] for k in ['parent_payload_read','ledger_audit_verified',
        'source_exit_verified','full_payload_sha_verified','actual8gpu_verified',
        'retirement_authorized','goal_complete'])


@pytest.mark.parametrize('fault',['unfinished','old_final','meta_id','rng_omission',
    'tokens','marker','report_scope','certificate_scope','world','sampler','config','replay',
    'outer_binding','rehashed_seed','wrong_output'])
def test_final_scope_rejections(tmp_path,parent_metadata,fault):
    c,cfg,a,m,p,r,done=completed(tmp_path,parent_metadata)
    if fault=='unfinished': r['status']='running'
    elif fault=='old_final': m['step']=57000
    elif fault=='meta_id': m['training_contract']['id']='e'*64
    elif fault=='rng_omission': p['files'].pop();p['bytes']-=1
    elif fault=='tokens': r['trained_tokens']=119537664000
    elif fault=='marker': done['report_sha256']='e'*64
    elif fault=='report_scope': r['final_checkpoint']=str(tmp_path/'unrelated/ckpt_0050000')
    elif fault=='certificate_scope': p['checkpoint']=str(tmp_path/'local/ckpt_0050000')
    elif fault=='world': m['world_size']=7
    elif fault=='sampler': m['sampler_version']=1
    elif fault=='config': m['config']['global_tokens']=1
    elif fault=='replay': m['step']=50001
    elif fault=='outer_binding': c['budget_execution']['parent_step']=16500
    elif fault=='rehashed_seed':
        a['config']['seed']=999
        a['id']=identity({k:v for k,v in a.items() if k!='id'})
        m['training_contract']=deepcopy(a);p['training_contract_id']=a['id']
    elif fault=='wrong_output':
        cfg['out_dir']=str(tmp_path/'unrelated');m['config']=deepcopy(cfg)
    with pytest.raises(ValueError): verify_completed_metadata(c,cfg,a,m,p,r,done,
        report_sha256='a'*64,contract_sha256='b'*64)
