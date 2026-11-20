"""Prepare an owned50k bundle without stopping or changing the live57k source."""
from copy import deepcopy
import json
import math
from pathlib import Path
import uuid

from common.data_contracts import resolve,sha256_file
from scripts.seal_horizon_handoff import write_once,json_bytes,unchanged
from scripts.tiered_checkpoint_retention import make_policy,size_tree,exact_path
from scripts.extended_horizon_storage import enforce_shared_space,GIB
from scripts.independent_resume_process_handoff import capture_extended_producer
from scripts.horizon_process_handoff import process_record
from scripts.budget_final_lineage import intervals
from scripts.completed_budget_storage import DEFERRED,METADATA_ALLOWANCE,SCOPE
from train.budget_horizon_contract import candidate

SOURCE='runs/3b_full_57k_v2'
MAIN='runs/3b_full_50k_v1'
CONTROL='runs/3b_50k_transition_v1'
AUTH='runs/main_training_50k_plan_v1/execution_authorization_v1.json'
OUTPUTS=dict(final_native='runs/3b_50k_final_native_v1',final_resume='runs/3b_50k_final_resume_v1',
             final_retention='runs/3b_50k_final_retention_v1',hf_and_serving='artifacts/3b_base_50k_v1',
             observer='runs/3b_50k_observer_v1',probe_continuous='runs/3b_50k_probe_continuous_v1',
             probe_split='runs/3b_50k_probe_split_v1',transition=CONTROL)


def storage_candidate(old, *, run_id):
    """Disjoint shared budgets include all existing allocations, no deletion credit."""
    main=resolve(MAIN);source=resolve(SOURCE)
    if resolve(old['main_run'])!=source or old['target_steps']!=57000:
        raise ValueError('Exact active57k source required')
    expected=old['expected_checkpoint_bytes']
    scratch=Path(old['tiered_retention']['temp_root']).parent
    policy=make_policy(main,scratch,expected,run_id=run_id)
    protected=math.ceil(expected*1.1)+GIB
    history_budget=math.ceil(4*expected*1.1)+3*protected+4*GIB
    policy.update(version=2,nfs_keep=3,adaptive_local_pruning=True,
                  copy_workers=old['tiered_retention']['copy_workers'],hash_workers=8,
                  protected_parent_budget_bytes=protected,nfs_budget_bytes=history_budget)
    shared=deepcopy(old['shared_storage']);pools=shared['nfs_pools']
    roots=[resolve(d['run']) for d in old['retention_branches']]
    if len(roots)!=3 or roots[-1]!=source:raise ValueError('Exact previous three owners required')
    group=pools['training_history_and_protected_parents']
    if [resolve(p) for p in group['paths']]!=roots:raise ValueError('Historical scope differs')
    group.update(paths=[str(p) for p in [*roots,main]],budget_bytes=history_budget)
    for name,path in OUTPUTS.items():
        prior=pools.pop(name)
        pools['preserved57k_'+name]=prior
        pools[name]=dict(paths=[str(resolve(path))],budget_bytes=METADATA_ALLOWANCE if name in DEFERRED else GIB)
    shared['local_history_paths'].append(policy['local_root'])
    shared['local_temp_paths'].append(policy['temp_root'])
    shared['local_history_budget_bytes']=max(shared['local_history_budget_bytes'],policy['local_budget_bytes'])
    deferred={name:dict(paths=[str(resolve(OUTPUTS[name]))],budget_bytes=gib*GIB)
              for name,gib in zip(DEFERRED,(8,1,1,100))}
    return dict(tiered_retention=policy,shared_storage=shared,expected_checkpoint_bytes=expected,
                protected_parent_budget_bytes=protected,durable_budget_bytes=history_budget,
                deferred_pools=deferred)


def prepare(*, boundary, supervisor_pid, worker_pid, controller_pids):
    from scripts.budget_transition_control import validate_controllers
    intervals(boundary)
    source,main,control,authpath=map(resolve,(SOURCE,MAIN,CONTROL,AUTH))
    approval=json.loads(authpath.read_text())
    if (approval.get('authorized') is not True or approval.get('target_steps')!=50000
            or approval.get('checkpoint_boundary_stop_authorized') is not True
            or approval.get('retention_transfer_authorized') is not True
            or resolve(approval['source_run'])!=source or resolve(approval['new_run'])!=main):
        raise ValueError('Latest50k transition authorization required')
    old=json.loads((source/'contract.json').read_text());unchanged({**old['code'],**old['evidence']})
    capture=capture_extended_producer(source,supervisor_pid,worker_pid)
    controllers={name:process_record(pid) for name,pid in controller_pids.items()};validate_controllers(controllers)
    logged=[json.loads(line) for line in (source/'training/log.jsonl').read_text().splitlines() if line.strip()]
    if max([r['step'] for r in logged if 'tok_s' in r] or [0])>=boundary:
        raise ValueError('Choose a future certified500step before preparation')
    for path in (main,control,*[resolve(v) for k,v in OUTPUTS.items() if k!='transition']):
        if path.exists():raise ValueError('Preserve existing50k namespace: '+str(path))
    certs=sorted((source/'checkpoint_certificates').glob('ckpt_*.json'))
    latest=next(p for p in reversed(certs) if (source/'training'/p.stem/'meta.json').exists())
    metadata=json.loads((source/'training'/latest.stem/'meta.json').read_text())
    proposed,_=candidate(metadata)
    policy_plan=storage_candidate(old,run_id=uuid.uuid4().hex)
    shared={k:v for k,v in policy_plan.items() if k!='deferred_pools'}
    capacity=enforce_shared_space(main,shared)
    # Final code set is pinned only after all implementations/tests are ready.
    code={str(p.relative_to(resolve('.'))):sha256_file(p)
          for folder in ['scripts','train','inference','model','common','eval']
          for p in resolve(folder).rglob('*.py')}
    control.mkdir();main.mkdir();bundle=main/'preparation';bundle.mkdir()
    owner=dict(owner_id=shared['tiered_retention']['run_id'],source_run=str(source),new_run=str(main),
               boundary_step=boundary,target_steps=50000,preparation_only=True)
    write_once(main/'.preparation_owner.json',json_bytes(owner))
    for name in ('expected_tokens.json','probe/probe.json','probe/.done'):
        (main/name).parent.mkdir(parents=True,exist_ok=True);write_once(main/name,(source/name).read_bytes())
    cp=bundle/'source_capture.json';wp=bundle/'waiting_controllers.json'
    write_once(cp,json_bytes(capture));write_once(wp,json_bytes(controllers))
    exact_auth=dict(approval,boundary_step=boundary,ordered_runs=[d['run'] for d in old['retention_branches']]+[str(main)],
                    user_authorization=str(authpath),user_authorization_sha256=sha256_file(authpath))
    ap=bundle/'boundary_authorization.json';write_once(ap,json_bytes(exact_auth))
    revision=dict(authorized=True,nfs_keep=3,local_max_keep=15,adaptive_local_oldest_first=True,
        management_run=str(main),local_namespace=shared['tiered_retention']['local_root'],
        user_authorization=str(authpath),user_authorization_sha256=sha256_file(authpath))
    rp=bundle/'retention_revision.json';write_once(rp,json_bytes(revision))
    sp=bundle/'storage_authorization.json'
    write_once(sp,json_bytes(dict(authorized=True,scope=SCOPE,target_step=50000,
        restore_deferred_budget_before_final_work=True,deferred_pool_names=list(DEFERRED),
        user_authorization=str(authpath),user_authorization_sha256=sha256_file(authpath))))
    cfg=dict(metadata['config'],max_steps=50000,keep_ckpts=50001,out_dir=str(main/'training'),stop_after=None)
    write_once(bundle/'main.json',json_bytes(cfg))
    draft={k:deepcopy(old[k]) for k in ('framework','gpu_identity','prompts','tokenizer','max_seconds',
        'max_pss_gib','memory_metric','local_budget_gib','reserve_gib','archive_required_before_prune','retention_policy')}
    draft.update(shared)
    draft.update(purpose='budget_preparation_not_executable',intended_run=str(main),main_run=str(main),
        source_run=str(source),source_contract_sha256=sha256_file(source/'contract.json'),boundary_step=boundary,
        target_steps=50000,requested_target_steps=50000,requested_target_tokens=50000*2097152,
        generation_steps=proposed['monitored_evaluation']['generation_steps'],
        configurations={'main.json':sha256_file(bundle/'main.json')},code=code,evidence=deepcopy(old['evidence']),
        retention_authorization=str(ap),retention_authorization_sha256=sha256_file(ap),
        retention_revision={'authorization':str(rp),'authorization_sha256':sha256_file(rp)},
        diagnostic_roots={k:str(resolve(OUTPUTS['probe_'+k])) for k in ('continuous','split')},
        storage_phase_revision=dict(version=1,scope=SCOPE,authorization=str(sp),target_step=50000,
            restore_before_final_stage=True,unused_pool_metadata_cap_bytes=METADATA_ALLOWANCE,
            deferred_pools=policy_plan['deferred_pools']))
    branches=deepcopy(old['retention_branches'])
    branches[-1].update(last_step=boundary,contract_sha256=sha256_file(source/'contract.json'),stopped_capture=capture)
    branches.append(dict(run=str(main),policy=draft['tiered_retention'],first_step=boundary+500,last_step=50000))
    draft['retention_branches']=branches
    for p in (authpath,resolve('runs/main_training_50k_plan_v1/diagnostic_cleanup_authorization_v1.json'),ap,cp,wp,rp,sp,bundle/'main.json',main/'.preparation_owner.json'):
        draft['evidence'][str(p)]=sha256_file(p)
    write_once(bundle/'draft.json',json_bytes(draft));write_once(bundle/'capacity_preparation.json',json_bytes(capacity))
    return dict(status='prepared50k_bundle_not_armed',main=str(main),draft=str(bundle/'draft.json'),
                capture=str(cp),controllers=str(wp),authorization=str(ap),boundary_step=boundary,
                source_stopped=False,actual8gpu_verified=False)
