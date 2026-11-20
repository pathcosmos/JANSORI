"""Bind four isolated local diagnostic modes sharing one protected NFS parent."""
from copy import deepcopy
import json
from pathlib import Path
from common.data_contracts import resolve,identity,sha256_file
from scripts.tiered_checkpoint_retention import exact_path
from scripts.seal_horizon_handoff import write_once,json_bytes,unchanged
from train.budget_horizon_contract import bound_candidate,BRIDGE_CODE
from train.budget_resume_probe import validate_plan,check_spec
from train.budget_horizon_handoff import validate_configuration
from train.probe import verify_probe

CODE={'scripts/prepare_budget_probe.py','scripts/run_budget_probe.py','train/monitored.py',
      'train/budget_resume_probe.py','train/budget_baseline_worker.py',
      'train/horizon_probe_observer.py','scripts/horizon_probe_process.py',
      'scripts/verify_horizon_probe.py','scripts/verify_horizon_observations.py',
      'scripts/verify_final_resume_probe.py'}|BRIDGE_CODE


def prepare(main,draft_path,capture_path):
    main,draft_path,capture_path=[exact_path(resolve(p)) for p in (main,draft_path,capture_path)]
    draft=json.loads(draft_path.read_text())
    if draft.get('purpose')!='budget_preparation_not_executable' or resolve(draft['intended_run'])!=main:
        raise ValueError('Exact prepared50k draft required')
    unchanged({**draft['code'],**draft['evidence']})
    receipt=json.loads((main/'parent_handoff.json').read_text());meta=receipt['parent_metadata']
    if not 10000<meta['step']<42500 or meta['step']%500 or receipt['source_capture']!=json.loads(capture_path.read_text()):
        raise ValueError('Diagnostic must use the sealed source boundary capture')
    child=bound_candidate(meta,receipt['execution_binding'])
    roots={k:exact_path(resolve(draft['diagnostic_roots'][k])) for k in ('continuous','split')}
    temp=exact_path(draft['tiered_retention']['temp_root']);source=exact_path(resolve(draft['source_run']))
    plan=dict(purpose='bounded_native_budget_resume_probe',execution_authorized=True,world_size=8,
        target_steps=50000,main_target_modified=False,parent_step=meta['step'],
        parent_contract_id=meta['training_contract']['id'],child_contract_id=child['id'],
        framework=draft['framework'],roots={k:str(v) for k,v in roots.items()},modes={},inputs={},
        code={**draft['code'],**{p:sha256_file(resolve(p)) for p in CODE}},
        max_seconds=3600,max_pss_gib=384,main_root=str(main),draft=str(draft_path),capture=str(capture_path),
        diagnostic_tokens_added_to_main=0,diagnostic_temp_root=str(temp),payload_roots={})
    contracts={};mode_roots=dict(baseline=roots['continuous']/'baseline',continuous=roots['continuous'],
                                first=roots['split'],resumed=roots['split'])
    for branch in roots.values():
        if branch.exists():raise ValueError('Preserve existing diagnostic namespace; explicit recovery required')
        branch.mkdir()
        write_once(branch/'parent_handoff.json',json_bytes(receipt))
        write_once(branch/'parent_certificate.json',(main/'parent_certificate.json').read_bytes())
        c={k:deepcopy(draft[k]) for k in ('code','evidence','framework','prompts','tokenizer','gpu_identity',
                                         'shared_storage','expected_checkpoint_bytes','retention_branches')}
        c.update(purpose='budget_horizon_validation',main_training_authorized=True,diagnostic_only=True,
            new_checkpoint_pruning_authorized=False,main_run=str(main),diagnostic_temp_root=str(temp),
            configurations={},generation_steps=child['monitored_evaluation']['generation_steps'],
            budget_execution=receipt['execution_binding'])
        c['code'].update(plan['code']);c['evidence'][str(branch/'parent_handoff.json')]=sha256_file(branch/'parent_handoff.json')
        contracts[branch]=c
    baseline=mode_roots['baseline'];baseline.mkdir()
    contracts[baseline]=dict(purpose='budget_baseline_validation',diagnostic_only=True,
        configurations={},code=deepcopy(plan['code']),evidence=deepcopy(draft['evidence']),
        framework=draft['framework'],prompts=draft['prompts'],tokenizer=draft['tokenizer'],
        generation_steps=meta['training_contract']['monitored_evaluation']['generation_steps'],
        baseline_training_contract=meta['training_contract'],parent_step=meta['step'])
    for mode,root in mode_roots.items():
        payload=temp/root.name if mode!='baseline' else temp/roots['continuous'].name/'baseline'
        plan['payload_roots'][mode]=str(payload)
        config=dict(meta['config'],out_dir=str(payload/'training'),stop_after=meta['step']+1 if mode=='first' else meta['step']+2,
                    max_steps=57000 if mode=='baseline' else 50000,keep_ckpts=57001 if mode=='baseline' else 50001,save_final=mode!='baseline')
        cp=root/(mode+'.json');write_once(cp,json_bytes(config))
        contracts[root]['configurations'][cp.name]=sha256_file(cp)
        plan['modes'][mode]=dict(run_dir=str(root),config=str(cp),attempt='horizon-probe-'+mode,
                                 observations=str(root/'observations'))
        plan['inputs'][str(cp)]=sha256_file(cp)
        if mode!='baseline':validate_configuration(config,contracts[root])
    for root,c in contracts.items():
        (root/'probe').mkdir()
        for name in ('expected_tokens.json','probe/probe.json','probe/.done'):
            write_once(root/name,(source/name).read_bytes());plan['inputs'][str(root/name)]=sha256_file(root/name)
        verify_probe(root/'probe');write_once(root/'contract.json',json_bytes(c))
        plan['inputs'][str(root/'contract.json')]=sha256_file(root/'contract.json');plan['inputs'].update(c['evidence'])
    for p in (draft_path,capture_path,main/'parent_handoff.json'):
        plan['inputs'][str(p)]=sha256_file(p)
    validate_plan(plan);spec=dict(plan=plan,plan_id=identity(plan))
    for mode in ('baseline','continuous','first'):check_spec(spec,mode)
    out=main/'horizon_probe';out.mkdir()
    write_once(out/'plan.json',json_bytes(plan))
    for mode in ('baseline','continuous','first'):write_once(out/(mode+'.json'),json_bytes(spec))
    write_once(out/'preparation.json',json_bytes(dict(status='prepared_bounded_budget_probe_not_executed',plan_id=identity(plan))))
    return plan


def resume_spec(plan_path):
    plan=json.loads(exact_path(resolve(plan_path)).read_text());validate_plan(plan)
    root=exact_path(resolve(plan['modes']['resumed']['run_dir']));cert=root/'certificates/first.json'
    proof=json.loads(cert.read_text())
    if proof.get('status')!='certified_horizon_probe_checkpoint' or proof.get('cuda_rng_required') is not True:
        raise ValueError('Fresh complete CUDA checkpoint certification required')
    spec=dict(plan=plan,plan_id=identity(plan),resume_certificate=str(cert),
              resume_certificate_sha256=sha256_file(cert),resume_stats=proof['stats'])
    check_spec(spec,'resumed');out=resolve(plan['main_root'])/'horizon_probe/resumed.json'
    write_once(out,json_bytes(spec));return out
