"""Publish the50k execution contract only after actual four-mode8GPU acceptance.

No process launch. Old contracts and all protected/diagnostic state are retained.
"""
from copy import deepcopy
import json
from common.data_contracts import resolve,sha256_file,identity
from scripts.tiered_checkpoint_retention import exact_path
from scripts.seal_horizon_handoff import unchanged,write_once,json_bytes,require_parent_absent
from scripts.extended_horizon_storage import enforce_shared_space
from train.budget_horizon_handoff import validate_configuration,load_handoff,require_controllers_absent
from scripts.supervise_budget_training import require_authorization,validate_parent_budget
from scripts.budget_horizon_retention import BudgetRetention


def activation(root,draft_path,capture_path,authorization_path):
    root,draft_path,capture_path,authorization_path=map(lambda p:exact_path(resolve(p)),
        (root,draft_path,capture_path,authorization_path))
    d=json.loads(draft_path.read_text());a=json.loads(authorization_path.read_text())
    source=resolve(d['source_run']);capture=json.loads(capture_path.read_text())
    if (d.get('purpose')!='budget_preparation_not_executable' or resolve(d['intended_run'])!=root or
            d.get('requested_target_steps')!=50000 or a.get('authorized') is not True or
            a.get('retention_transfer_authorized') is not True or a.get('checkpoint_boundary_stop_authorized') is not True or
            a.get('target_steps')!=50000 or a.get('boundary_step')!=d.get('boundary_step') or
            resolve(a['source_run'])!=source or resolve(a['new_run'])!=root):
        raise ValueError('Exact authorized certified-boundary-to50000 scope required')
    from scripts.budget_final_lineage import intervals
    intervals(a['boundary_step'])
    unchanged({**d['code'],**d['evidence']});require_parent_absent(capture,source)
    if sha256_file(source/'contract.json')!=d['source_contract_sha256']:raise ValueError('Source contract changed')
    from scripts.run_budget_probe import load_plan,verify_certificates
    plan,probe_root=load_plan(root/'horizon_probe/plan.json');report_path=probe_root/'report.json'
    report=json.loads(report_path.read_text());marker=json.loads((probe_root/'.done').read_text())
    if (marker!=dict(plan_id=identity(plan),report_sha256=sha256_file(report_path)) or
            report.get('status')!='passed_actual_8rank_budget_resume' or report.get('plan_id')!=identity(plan) or
            report.get('main_committed_tokens_added')!=0 or report.get('main_target_modified') is not False):
        raise ValueError('Actual completed8GPU extension validation missing')
    unchanged(report['evidence']);verify_certificates(plan);enforce_shared_space(root,d)
    receipt=json.loads((root/'parent_handoff.json').read_text())
    if receipt['source_capture']!=capture or receipt['parent_metadata']['step']!=a['boundary_step']:
        raise ValueError('Sealed source/boundary differs')
    require_controllers_absent(receipt['stopped_waiting_controllers'])
    c=deepcopy(d)
    for key in ('intended_run','requested_target_steps','requested_target_tokens','pending','diagnostic_roots'):
        c.pop(key,None)
    c.update(purpose='main_training',main_training_authorized=True,new_checkpoint_pruning_authorized=True,
        budget_execution=receipt['execution_binding'],budget_validation_report=str(report_path),
        main_run=str(root),target_steps=50000,target_tokens=50000*2097152)
    config_path=draft_path.parent/'main.json';cfg=json.loads(config_path.read_text())
    if sha256_file(config_path)!=d['configurations']['main.json']:raise ValueError('Configuration changed')
    c['evidence'].update({str(p):sha256_file(p) for p in
        (authorization_path,root/'parent_handoff.json',report_path,probe_root/'.done')})
    validate_configuration(cfg,c);require_authorization(c)
    _,_,proof=load_handoff(root,c,cfg);validate_parent_budget(c,proof)
    BudgetRetention(root,c,authorized=True).require_stopped()
    for name in ('attempts','ledgers','checkpoint_certificates','training'):(root/name).mkdir(exist_ok=True)
    write_once(root/'main.json',config_path.read_bytes());write_once(root/'contract.json',json_bytes(c))
    enforce_shared_space(root,c)
    result=dict(status='authorized50k_execution_materialized_not_launched',parent_step=a['boundary_step'],
        target_steps=50000,target_tokens=50000*2097152,main_config_sha256=sha256_file(root/'main.json'),
        contract_sha256=sha256_file(root/'contract.json'),actual_probe_report_sha256=sha256_file(report_path),
        source_run=str(source),new_run=str(root),goal_complete=False)
    write_once(root/'activation.json',json_bytes(result));return result
