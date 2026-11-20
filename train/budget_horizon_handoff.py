"""Admission for a certified 50k fourth-generation handoff, never a launcher."""
import json
from pathlib import Path

from common.data_contracts import resolve, sha256_file, identity
from train.parent_checkpoint_resume import canonical
from train.budget_horizon_contract import BRIDGE_CODE, bound_candidate, verify_bridge_files
from train.budget_parent_resume import BudgetParentResume
from scripts.budget_horizon_lineage import verify_lineage
from scripts.seal_horizon_handoff import require_parent_absent
from scripts.tiered_checkpoint_retention import inventory
from train.final_resume_probe import file_stats
from scripts.extended_horizon_storage import enforce_shared_space


def validate_configuration(config, contract):
    from scripts.plan_main_training import geometry
    result=geometry(config)
    expected=dict(model='3b',vocab_size=65536,seq_len=4096,micro_bsz=4,
        global_tokens=2097152,compile=False,sampler_version=2,max_steps=50000,
        lr=.0003,warmup=1000,decay_frac=.15,min_lr_ratio=.1,
        eval_every=250,ckpt_every=500,save_final=True,log_every=10)
    if any(type(config.get(k)) is not type(v) or config[k]!=v for k,v in expected.items()):
        raise ValueError('Unreviewed 50k training geometry/cadence')
    if type(config.get('keep_ckpts')) is not int or config['keep_ckpts']!=50001:
        raise ValueError('50k native pruning must remain disabled')
    if contract.get('main_training_authorized') is not True:
        raise ValueError('Missing 50k training authorization')
    purpose=contract.get('purpose')
    if purpose=='main_training':
        if config.get('stop_after') is not None:raise ValueError('50k production stop_after differs')
    elif purpose=='budget_horizon_validation':
        begin=contract['budget_execution']['parent_step']
        if type(config.get('stop_after')) is not int or not begin<config['stop_after']<=begin+2:
            raise ValueError('50k diagnostic must stop after one/two absolute updates')
    else:raise ValueError('Unknown 50k execution purpose')
    schedule=contract.get('generation_steps')
    if (not isinstance(schedule,list) or not schedule or schedule!=sorted(set(schedule))
            or any(type(s) is not int or not 0<s<=50000 or s%250 for s in schedule)
            or schedule[-1]!=50000):
        raise ValueError('50k generation schedule differs')
    return result


def require_controllers_absent(rows):
    if set(rows)!={'observer','followup'} or len({r['pid'] for r in rows.values()})!=2:
        raise ValueError('Exact previous observer and completed-followup identities required')
    for row in rows.values():
        pid,ticks=row['pid'],row['start_ticks']
        if type(pid) is not int or pid<=1 or type(ticks) is not int or ticks<=0:
            raise ValueError('Invalid previous controller identity')
        try:stat=Path(f'/proc/{pid}/stat').read_text()
        except FileNotFoundError:continue
        # A zombie cannot spawn consumers, but remains bound evidence until reaped.
        if int(stat.rsplit(')',1)[1].split()[19])==ticks:
            raise ValueError('Previous controller still present')


def load_handoff(root, contract, config):
    root=canonical(root);main=canonical(resolve(contract['main_run']))
    if root.parent!=canonical(resolve('runs')) or main.parent!=root.parent:
        raise ValueError('Exact independent permanent 50k namespaces required')
    if root.stat().st_dev!=main.stat().st_dev:raise ValueError('Run metadata must remain on NFS')
    out=canonical(resolve(config['out_dir']))
    if contract['purpose']=='main_training':
        if root!=main or out!=main/'training':raise ValueError('50k production namespace differs')
    else:
        temp=canonical(contract['diagnostic_temp_root'])
        if root==main or out!=temp/root.name/'training' or str(temp) not in contract['shared_storage']['local_temp_paths']:
            raise ValueError('50k diagnostic outside reserved local pool')
        ancestor=temp
        while not ancestor.exists():ancestor=ancestor.parent
        if ancestor.stat().st_dev==main.stat().st_dev:raise ValueError('Diagnostic output must use local disk')
    path=root/'parent_handoff.json'
    if contract['evidence'].get(str(path))!=sha256_file(path):raise ValueError('Unbound 50k handoff')
    receipt=json.loads(path.read_text());binding=contract['budget_execution']
    if (receipt.get('status')!='sealed_budget_parent_handoff' or receipt.get('execution_binding')!=binding
            or canonical(resolve(receipt['protection_root']))!=main):
        raise ValueError('Unsealed or mismatched 50k handoff')
    verify_bridge_files(binding)
    if any(contract['code'].get(n)!=binding['code'][n] for n in BRIDGE_CODE):
        raise ValueError('Outer/inner 50k implementation bindings differ')
    for name,digest in {**contract['code'],**contract['evidence']}.items():
        if sha256_file(canonical(resolve(name)))!=digest:raise ValueError('Bound 50k evidence changed: '+name)
    meta=receipt['parent_metadata'];candidate=bound_candidate(meta,binding)
    if contract['generation_steps']!=candidate['monitored_evaluation']['generation_steps']:
        raise ValueError('Outer 50k generation schedule differs')
    parent=canonical(resolve(receipt['parent_checkpoint']))
    if parent!=main/'parent_checkpoint'/f"ckpt_{meta['step']:07d}" or parent.stat().st_dev!=main.stat().st_dev:
        raise ValueError('50k parent outside protected permanent namespace')
    proof_path=main/'parent_certificate.json'
    if sha256_file(proof_path)!=binding['parent_certificate_sha256']:raise ValueError('Parent certificate bytes changed')
    proof=json.loads(proof_path.read_text())
    if (identity(proof)!=binding['parent_certificate_id'] or proof.get('step')!=meta['step']
            or proof.get('training_contract_id')!=meta['training_contract']['id']):
        raise ValueError('50k parent certificate identity differs')
    inventory(parent,proof)
    if file_stats(parent)!=receipt['parent_stats'] or json.loads((parent/'meta.json').read_text())!=meta:
        raise ValueError('50k protected parent changed after SHA verification')
    definitions=contract['retention_branches']
    if len(definitions)!=4 or canonical(resolve(definitions[-1]['run']))!=main:
        raise ValueError('Missing four-generation namespace binding')
    historical=[canonical(resolve(d['run'])) for d in definitions[:3]]
    source=canonical(resolve(receipt['source_run']))
    if len(set(historical+[main]))!=4 or source!=historical[-1] or any(p.parent!=main.parent for p in historical):
        raise ValueError('Four distinct permanent run namespaces required')
    for previous,d in zip(historical,definitions[:3]):
        require_parent_absent(d['stopped_capture'],previous)
    if receipt['source_capture']!=definitions[2]['stopped_capture']:
        raise ValueError('Stopped 57k source identity differs')
    require_controllers_absent(receipt['stopped_waiting_controllers'])
    lineage=main/'lineage'
    if sha256_file(lineage/'manifest.json')!=binding['parent_ledger_manifest_sha256']:
        raise ValueError('Three-prefix 50k lineage manifest changed')
    probe=json.loads((root/'probe/probe.json').read_text())
    prompts=[json.loads(s) for s in resolve(contract['prompts']).read_text().splitlines() if s.strip()]
    if (sha256_file(root/'probe/probe.json')!=candidate['monitored_evaluation']['probe_sha256']
            or sha256_file(resolve(contract['prompts']))!=candidate['monitored_evaluation']['prompts_sha256']):
        raise ValueError('50k probe/prompt identity differs')
    manifest,_=verify_lineage(lineage,meta,probe,prompts)
    if canonical(resolve(manifest['source_run']))!=source:raise ValueError('50k lineage source differs')
    if contract['purpose']=='main_training':
        path=canonical(resolve(contract['budget_validation_report']))
        if contract['evidence'].get(str(path))!=sha256_file(path):raise ValueError('Actual 8GPU report unbound')
        validation=json.loads(path.read_text())
        if (validation.get('status')!='passed_actual_8rank_budget_resume' or validation.get('world_size')!=8
                or validation.get('parent_step')!=meta['step']
                or validation.get('parent_contract_id')!=meta['training_contract']['id']
                or validation.get('child_contract_id')!=candidate['id']
                or any(validation.get(k) is not True for k in
                    ['model_optimizer_rng_exact','next_update_exact','split_resume_exact'])
                or not validation.get('evidence')):
            raise ValueError('Actual 8GPU 50k validation incomplete')
        for name,digest in validation['evidence'].items():
            if sha256_file(canonical(resolve(name)))!=digest:raise ValueError('8GPU evidence changed')
    enforce_shared_space(main,contract)
    return receipt,candidate,proof


def parent_resume(receipt, candidate, proof, config, contract):
    return BudgetParentResume(parent_checkpoint=resolve(receipt['parent_checkpoint']),
        destination=resolve(config['out_dir']),parent_metadata=receipt['parent_metadata'],
        child_contract=candidate,parent_proof=proof,verified_parent_stats=receipt['parent_stats'],
        execution_binding=contract['budget_execution'])
