"""Materialize an explicitly authorized35k contract after actual8GPU acceptance.

Does not launch or signal processes. Exact same-content retry is allowed;
foreign configs and prior failure evidence are never overwritten.
"""
from copy import deepcopy
import json
from pathlib import Path

from common.data_contracts import resolve,sha256_file,identity,artifact_path
from scripts.tiered_checkpoint_retention import exact_path
from scripts.seal_horizon_handoff import unchanged,write_once,json_bytes,require_parent_absent
from scripts.horizon_storage_plan import enforce_runtime_space
from train.horizon_target import TARGET_STEPS,TOTAL_TOKENS
from train.monitored_horizon import validate_configuration,load_handoff
from scripts.supervise_horizon_training import require_authorization,validate_parent_budget
from scripts.horizon_retention import HorizonRetention


def activation(root,draft_path,capture_path,authorization_path):
    root,draft_path,capture_path,authorization_path=[exact_path(resolve(p)) for p in
        (root,draft_path,capture_path,authorization_path)]
    draft=json.loads(draft_path.read_text());approval=json.loads(authorization_path.read_text())
    source=exact_path(resolve(draft['source_run']))
    if (draft['purpose']!='horizon_preparation_not_executable' or resolve(draft['intended_run'])!=root or
            draft['requested_target_steps']!=TARGET_STEPS or not approval.get('authorized') or
            approval.get('new_run_training_authorized') is not True or approval.get('retention_transfer_authorized') is not True or
            approval.get('target_steps')!=TARGET_STEPS or resolve(approval['source_run'])!=source or
            resolve(approval['new_run'])!=root):
        raise ValueError('Explicit exact35k transition authorization required')
    unchanged({**draft['code'],**draft['evidence']})
    if sha256_file(source/'contract.json')!=draft['source_contract_sha256']:raise ValueError('Source contract changed')
    capture=json.loads(capture_path.read_text());require_parent_absent(capture,source)
    from scripts.run_horizon_probe import load_plan,verify_certificates
    plan,probe_root=load_plan(root/'horizon_probe/plan.json')
    marker=json.loads((probe_root/'.done').read_text());report_path=probe_root/'report.json'
    report=json.loads(report_path.read_text())
    if (marker!=dict(plan_id=identity(plan),report_sha256=sha256_file(report_path)) or
            report.get('status')!='passed_actual_8rank_horizon_resume' or report.get('plan_id')!=identity(plan) or
            report.get('main_committed_tokens_added')!=0 or report.get('main_target_modified') is not False):
        raise ValueError('Actual completed8GPU diagnostic certificate missing or changed')
    unchanged(report['evidence']);verify_certificates(plan)
    enforce_runtime_space(root,draft)
    receipt=json.loads((root/'parent_handoff.json').read_text())
    if capture['processes']!=receipt['stopped_parent_processes']:raise ValueError('Sealed source identities differ')
    old=json.loads((source/'contract.json').read_text())
    c=deepcopy(draft)
    for k in ['pending','activation_ready','authorization_note','requested_target_steps','requested_target_tokens','intended_run']:
        c.pop(k,None)
    c.update(purpose='main_training',main_training_authorized=True,new_checkpoint_pruning_authorized=True,
        horizon_execution=receipt['execution_binding'],horizon_validation_report=str(report_path),
        target_steps=TARGET_STEPS,target_tokens=TOTAL_TOKENS)
    config_path=draft_path.parent/'main.json';config=json.loads(config_path.read_text())
    if sha256_file(config_path)!=draft['configurations']['main.json']:raise ValueError('Draft configuration changed')
    validate_configuration(config,c)
    auth=dict(version=1,user_message=approval['user_instruction'],run_dir=str(root),
        user_authorization=str(authorization_path),user_authorization_sha256=sha256_file(authorization_path),
        main_training_authorized=True,new_checkpoint_pruning_authorized=True,
        config_sha256=sha256_file(config_path),training_target_steps=TARGET_STEPS,training_target_tokens=TOTAL_TOKENS)
    retention=dict(authorized=True,nfs_keep=3,local_max_keep=15,adaptive_local_oldest_first=True,
        management_run=str(root),local_namespace=c['tiered_retention']['local_root'],
        user_authorization=str(authorization_path),user_authorization_sha256=sha256_file(authorization_path),
        global_history_across=[str(source),str(root)])
    write_once(root/'authorization.json',json_bytes(auth));write_once(root/'retention_authorization.json',json_bytes(retention))
    c['retention_revision']=dict(authorization=str(root/'retention_authorization.json'),
        authorization_sha256=sha256_file(root/'retention_authorization.json'))
    c['legacy_retention']=dict(source_run=str(source),source_policy=old['tiered_retention'],
        source_contract_sha256=sha256_file(source/'contract.json'),parent_step=receipt['parent_metadata']['step'],
        stopped_capture=capture,authorization=str(authorization_path),authorization_sha256=sha256_file(authorization_path))
    c['code'].update({p:sha256_file(resolve(p)) for p in ['scripts/activate_horizon_training.py','scripts/horizon_retention.py']})
    c['evidence'].update({str(p):sha256_file(p) for p in [authorization_path,root/'authorization.json',
        root/'retention_authorization.json',root/'parent_handoff.json',report_path,probe_root/'.done']})
    require_authorization(c)
    _,_,proof=load_handoff(root,c,config);validate_parent_budget(c,proof)
    HorizonRetention(root,c,authorized=True).require_stopped()
    for name in ['expected_tokens.json','probe/probe.json','probe/.done']:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
        write_once(path,(draft_path.parent/name).read_bytes())
    (root/'attempts').mkdir(exist_ok=True);(root/'ledgers').mkdir(exist_ok=True)
    (root/'checkpoint_certificates').mkdir(exist_ok=True);(root/'training').mkdir(exist_ok=True)
    write_once(root/'main.json',config_path.read_bytes());write_once(root/'contract.json',json_bytes(c))
    enforce_runtime_space(root,c)
    result=dict(status='authorized35k_execution_materialized_not_launched',parent_step=receipt['parent_metadata']['step'],
        target_steps=TARGET_STEPS,target_tokens=TOTAL_TOKENS,main_config_sha256=sha256_file(root/'main.json'),
        contract_sha256=sha256_file(root/'contract.json'),actual_probe_report_sha256=sha256_file(report_path),
        legacy_run=str(source),new_run=str(root),goal_complete=False)
    write_once(root/'activation.json',json_bytes(result))
    return result
