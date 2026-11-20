"""Materialize an owned, non-executable57k preparation bundle. No signals."""
import json
from copy import deepcopy
from common.data_contracts import resolve,sha256_file,identity
from scripts.seal_horizon_handoff import write_once,json_bytes,unchanged
from scripts.extended_horizon_process_handoff import capture_horizon_producer
from scripts.horizon_process_handoff import process_record
from scripts.extended_boundary_handoff import validate_controllers,CONTROLLER_SCOPE
from scripts.extended_horizon_storage import enforce_shared_space
from scripts.prepare_extended_probe import CODE as PROBE_CODE
from train.extended_horizon_worker_contract import BRIDGE_CODE
from train.extended_horizon_transition import build_extension_candidate

CODE=BRIDGE_CODE|PROBE_CODE|{row[0] for row in CONTROLLER_SCOPE.values()}|{'scripts/prepare_extended_run.py','scripts/extended_boundary_handoff.py',
    'scripts/seal_extended_handoff.py','scripts/horizon_boundary_handoff.py','scripts/horizon_process_handoff.py',
    'scripts/protect_horizon_parent.py','scripts/extended_horizon_process_handoff.py',
    'scripts/extended_horizon_retention.py','scripts/horizon_retention.py','scripts/activate_extended_training.py',
    'scripts/supervise_extended_training.py','scripts/run_extended_transition.py'}


def prepare(plan_dir,*,supervisor_pid,worker_pid,controller_pids):
    directory=resolve(plan_dir);source=resolve('runs/3b_full_35k_v1');main=resolve('runs/3b_full_57k_v2')
    approval_path=directory/'boundary10000_execution_authorization_v1.json'
    approval=json.loads(approval_path.read_text())
    if (approval.get('authorized') is not True or approval.get('boundary_step')!=10000 or
            approval.get('target_steps')!=57000 or resolve(approval['source_run'])!=source or
            resolve(approval['new_run'])!=main):raise ValueError('Latest exact user authorization required')
    old=json.loads((source/'contract.json').read_text());unchanged({**old['code'],**old['evidence']})
    capture=capture_horizon_producer(source,supervisor_pid,worker_pid)
    controllers={name:process_record(pid) for name,pid in controller_pids.items()};validate_controllers(controllers)
    shared=json.loads((directory/'shared_storage_plan_v1.json').read_text())['shared_contract']
    original_root=resolve(old['legacy_retention']['source_run']);original=json.loads((original_root/'training/training_contract.json').read_text())
    # Shape/schedule preparation uses the latest certified metadata only; the
    # actual10000 binding is created after protection and stopped-prefix seal.
    certs=sorted((source/'checkpoint_certificates').glob('ckpt_*.json'))
    latest=next(p for p in reversed(certs) if (source/'training'/p.stem/'meta.json').exists())
    metadata=json.loads((source/'training'/latest.stem/'meta.json').read_text())
    candidate,_=build_extension_candidate(metadata,original)
    owner=dict(owner_id=shared['tiered_retention']['run_id'],source_run=str(source),new_run=str(main),
               boundary_step=10000,target_steps=57000,preparation_only=True)
    if main.exists():raise ValueError('New namespace already exists; inspect exact owner before any retry')
    # Capacity is measured before creating new owned directories.
    capacity=enforce_shared_space(main,shared)
    main.mkdir();write_once(main/'.preparation_owner.json',json_bytes(owner))
    bundle=main/'preparation';bundle.mkdir()
    for name in ('expected_tokens.json','probe/probe.json','probe/.done'):
        (main/name).parent.mkdir(parents=True,exist_ok=True);write_once(main/name,(source/name).read_bytes())
    cp=bundle/'source_capture.json';wp=bundle/'waiting_controllers.json'
    write_once(cp,json_bytes(capture));write_once(wp,json_bytes(controllers))
    auth=dict(authorized=True,nfs_keep=3,local_max_keep=15,adaptive_local_oldest_first=True,
        management_run=str(main),local_namespace=shared['tiered_retention']['local_root'],
        user_authorization=str(approval_path),user_authorization_sha256=sha256_file(approval_path))
    rp=bundle/'retention_revision.json';write_once(rp,json_bytes(auth))
    cfg=dict(metadata['config'],max_steps=57000,keep_ckpts=57001,out_dir=str(main/'training'),stop_after=None)
    write_once(bundle/'main.json',json_bytes(cfg))
    draft={k:deepcopy(old[k]) for k in ('framework','gpu_identity','prompts','tokenizer','max_seconds',
        'max_pss_gib','memory_metric','local_budget_gib','reserve_gib','archive_required_before_prune','retention_policy')}
    draft.update(deepcopy(shared));draft.update(purpose='extension_preparation_not_executable',
        intended_run=str(main),source_run=str(source),source_contract_sha256=sha256_file(source/'contract.json'),
        requested_target_steps=57000,requested_target_tokens=57000*2097152,
        generation_steps=candidate['monitored_evaluation']['generation_steps'],
        protected_parent_budget_bytes=shared['tiered_retention']['protected_parent_budget_bytes'],
        durable_budget_bytes=shared['tiered_retention']['nfs_budget_bytes'],
        configurations={'main.json':sha256_file(bundle/'main.json')},
        code={**old['code'],**{p:sha256_file(resolve(p)) for p in CODE}},evidence=deepcopy(old['evidence']),
        retention_authorization=str(approval_path),retention_authorization_sha256=sha256_file(approval_path),
        retention_revision={'authorization':str(rp),'authorization_sha256':sha256_file(rp)},
        diagnostic_roots={k:str(resolve('runs/3b_57k_probe_'+k+'_v2')) for k in ('continuous','split')})
    draft['retention_branches']=[dict(run=str(original_root),policy=old['legacy_retention']['source_policy'],
        first_step=500,last_step=8000,contract_sha256=sha256_file(original_root/'contract.json'),
        stopped_capture=old['legacy_retention']['stopped_capture']),dict(run=str(source),policy=old['tiered_retention'],
        first_step=8500,last_step=10000,contract_sha256=sha256_file(source/'contract.json'),stopped_capture=capture),
        dict(run=str(main),policy=draft['tiered_retention'],first_step=10500,last_step=57000)]
    for p in (approval_path,cp,wp,rp,bundle/'main.json',main/'.preparation_owner.json'):
        draft['evidence'][str(p)]=sha256_file(p)
    write_once(bundle/'draft.json',json_bytes(draft));write_once(bundle/'capacity_preparation.json',json_bytes(capacity))
    return dict(status='prepared57k_bundle_not_armed',main=str(main),draft=str(bundle/'draft.json'),
                capture=str(cp),controllers=str(wp),source_stopped=False,actual8gpu_verified=False)
