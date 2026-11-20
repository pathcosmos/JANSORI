"""Four bounded native-worker modes for an authenticated35k horizon diagnostic.

No controller/auto-launch: a separately admitted controller must supply sealed
parents, immutable plans, an idle8GPU lease and per-mode specifications. Baseline
keeps the original57k optimization identity, writes no full checkpoint, and
shares the continuous branch's protected parent read-only.
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve, identity, sha256_file
from scripts.tiered_checkpoint_retention import exact_path, inventory, atomic_json
from train.final_resume_probe import file_stats, save_state
from train.horizon_target import TARGET_STEPS, DECAY_START_STEP
from train.horizon_probe_observer import MODES, NativeStateObserver, observe_native


def checked_path(value):
    return exact_path(resolve(value))


def validate_plan(plan):
    if (plan.get('purpose')!='bounded_native_horizon_resume_probe' or
            plan.get('execution_authorized') is not True or plan.get('world_size')!=8 or
            plan.get('target_steps')!=TARGET_STEPS or plan.get('main_target_modified') is not False or
            set(plan.get('modes',{}))!=set(MODES)):
        raise ValueError('Incomplete/unapproved horizon diagnostic plan')
    step=plan['parent_step']
    if type(step) is not int or step<=0 or step%500 or step+2>=DECAY_START_STEP:
        raise ValueError('Diagnostic parent must be a saved boundary before changed LR history')
    continuous,split=[checked_path(plan['roots'][name]) for name in ['continuous','split']]
    if (continuous==split or any(p.parent!=resolve('runs') for p in [continuous,split]) or
            any(p.name in ('3b_full_v1','3b_full_35k_v1') for p in [continuous,split])):
        raise ValueError('Diagnostic branches must be distinct non-main run namespaces')
    expected={'baseline':continuous/'baseline','continuous':continuous,'first':split,'resumed':split}
    for mode,root in expected.items():
        row=plan['modes'][mode]
        if (checked_path(row['run_dir'])!=root or checked_path(row['config']).parent!=root or
                row['attempt']!=f'horizon-probe-{mode}' or
                checked_path(row['observations'])!=root/'observations'):
            raise ValueError('Diagnostic mode namespace/attempt differs')
    required={'train/horizon_resume_probe.py','train/horizon_probe_observer.py',
              'train/horizon_target.py','train/monitored.py','train/monitored_horizon.py'}
    if not required<=set(plan['code']):
        raise ValueError('Unbound diagnostic entry/worker implementation')
    return expected


def check_spec(spec, mode):
    from train.monitored_horizon import load_handoff, validate_configuration
    from train.runtime import RESUME_KEYS, complete_checkpoints
    plan=spec['plan']
    if spec['plan_id']!=identity(plan) or mode not in MODES:
        raise ValueError('Diagnostic plan identity or mode differs')
    paths=validate_plan(plan)
    for name,digest in {**plan['code'],**plan['inputs']}.items():
        if sha256_file(checked_path(name))!=digest:
            raise ValueError('Diagnostic immutable input/code changed: '+name)
    row=plan['modes'][mode];root=paths[mode];config_path=checked_path(row['config'])
    contract_path=root/'contract.json'
    if (plan['inputs'].get(str(config_path))!=sha256_file(config_path) or
            plan['inputs'].get(str(contract_path))!=sha256_file(contract_path)):
        raise ValueError('Diagnostic configuration/contract lacks explicit binding')
    config=json.loads(config_path.read_text());contract=json.loads(contract_path.read_text())
    parent_root=paths['continuous'] if mode=='baseline' else root
    parent_contract=json.loads((parent_root/'contract.json').read_text())
    if parent_contract.get('purpose')!='horizon_validation':
        raise ValueError('Parent diagnostic branch is not a validation-only contract')
    parent_config=json.loads(checked_path(plan['modes']['continuous' if mode=='baseline' else mode]['config']).read_text())
    receipt,candidate,proof=load_handoff(parent_root,parent_contract,parent_config)
    metadata=receipt['parent_metadata'];parent=checked_path(receipt['parent_checkpoint'])
    if (metadata['step']!=plan['parent_step'] or metadata['training_contract']['id']!=plan['parent_contract_id'] or
            candidate['id']!=plan['child_contract_id'] or
            checked_path(config['out_dir'])!=root/'training' or
            config.get('stop_after')!=plan['parent_step']+MODES[mode][1]):
        raise ValueError('Diagnostic parent/configuration/absolute boundary differs')
    if contract['configurations'].get(config_path.name)!=sha256_file(config_path):
        raise ValueError('Worker configuration is not bound')
    if mode=='baseline':
        allowed={'out_dir','stop_after','keep_ckpts','save_final'}
        original=metadata['config']
        if (set(config)!=set(original) or any(config[k]!=original[k] for k in config if k not in allowed) or
                any(config[k]!=metadata['training_contract']['config'][k] for k in RESUME_KEYS) or
                config['max_steps']!=57000 or config['keep_ckpts']<=57000 or config['save_final'] is not False or
                contract['generation_steps']!=metadata['training_contract']['monitored_evaluation']['generation_steps']):
            raise ValueError('Baseline must preserve the original57k trajectory without saving/pruning')
    else:
        if contract.get('purpose')!='horizon_validation':
            raise ValueError('Diagnostic cannot invoke a main-training contract')
        validate_configuration(config,contract)
    inventory(parent,proof)
    if file_stats(parent)!=receipt['parent_stats']:
        raise ValueError('Protected parent changed after certification')
    destination=root/'training'
    if mode=='resumed':
        selected=destination/f"ckpt_{plan['parent_step']+1:07d}"
        if complete_checkpoints(destination)!=[selected]:
            raise ValueError('Resume requires exactly the first diagnostic checkpoint')
        certificate=checked_path(spec['resume_certificate'])
        if (certificate!=root/'certificates/first.json' or
                sha256_file(certificate)!=spec['resume_certificate_sha256']):
            raise ValueError('Intermediate certification binding differs')
        resumed_proof=json.loads(certificate.read_text())
        if (resumed_proof['step']!=plan['parent_step']+1 or
                resumed_proof['training_contract_id']!=candidate['id'] or
                checked_path(resumed_proof['checkpoint'])!=selected):
            raise ValueError('Intermediate checkpoint identity differs')
        inventory(selected,resumed_proof)
        if file_stats(selected)!=spec['resume_stats']:
            raise ValueError('Intermediate checkpoint changed after fresh certification')
        selected_stats=spec['resume_stats']
    else:
        if destination.exists() and any(destination.iterdir()):
            raise ValueError('Preserve existing diagnostic output; use explicit recovery')
        selected,selected_stats=parent,receipt['parent_stats']
    observation=checked_path(row['observations'])/f'{mode}-rank{os.environ.get("RANK","0")}.json'
    if observation.exists():
        raise ValueError('Existing diagnostic observation must be preserved')
    return plan,root,config_path,config,receipt,candidate,proof,selected,selected_stats


def run(spec, mode):
    import torch
    from train import train as trainer
    from train.runtime import validate_resume
    plan,root,config_path,config,receipt,candidate,proof,selected,selected_stats=check_spec(spec,mode)
    rank=int(os.environ['RANK']);world=int(os.environ['WORLD_SIZE']);local=int(os.environ['LOCAL_RANK'])
    if world!=8 or not 0<=rank<8 or local!=rank:
        raise ValueError('Diagnostic requires actual single-node8rank launcher geometry')
    if plan['framework']!={'torch':str(torch.__version__),'cuda':torch.version.cuda}:
        raise ValueError('Diagnostic framework differs from original training')
    original_load=trainer.dcp.load;original_save=trainer.dcp.save
    original_scan=trainer.complete_checkpoints;original_validate=trainer.validate_resume
    original_run=trainer.run
    def record_runtime(c):
        import torch.distributed as dist
        if (dist.get_world_size()!=8 or dist.get_rank()!=rank or str(dist.get_backend())!='nccl'):
            raise ValueError('Actual initialized distributed group differs from8rank NCCL plan')
        torch.cuda.set_device(local)
        prop=torch.cuda.get_device_properties(local)
        if 'H200' not in prop.name or torch.cuda.device_count()!=8:
            raise ValueError('Actual diagnostic devices differ from the reviewed H200x8 hardware')
        path=root/'execution'/f'{mode}-rank{rank}.json'
        if path.exists():raise ValueError('Preserve prior diagnostic runtime receipt')
        stat=Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')',1)[1].split()
        atomic_json(path,dict(plan_id=spec['plan_id'],spec_id=identity(spec),mode=mode,rank=rank,
            world_size=dist.get_world_size(),backend=str(dist.get_backend()),pid=os.getpid(),
            parent_pid=os.getppid(),start_ticks=int(stat[19]),local_rank=local,
            device_name=prop.name,device_total_memory=prop.total_memory,
            device_uuid=str(prop.uuid) if hasattr(prop,'uuid') else None,
            framework={'torch':str(torch.__version__),'cuda':torch.version.cuda},
            diagnostic_only=True,actual8gpu_acceptance=False))
        return original_run(c)
    trainer.run=record_runtime
    def guarded_load(*args,**kwargs):
        if checked_path(kwargs['checkpoint_id'])!=selected or file_stats(selected)!=selected_stats:
            raise ValueError('Diagnostic DCP read outside the certified source')
        result=original_load(*args,**kwargs)
        if file_stats(selected)!=selected_stats:
            raise ValueError('Diagnostic checkpoint changed during DCP load')
        return result
    trainer.dcp.load=guarded_load
    if mode=='baseline':
        scanned=False
        def scan(directory):
            nonlocal scanned
            if checked_path(directory)!=root/'training':
                raise ValueError('Baseline checkpoint scan outside isolated output')
            if scanned:return []  # External parent can never enter native pruning.
            scanned=True
            if file_stats(selected)!=selected_stats:raise ValueError('Baseline parent changed')
            return [selected]
        def validate(meta,contract):
            if meta!=receipt['parent_metadata'] or contract!=meta['training_contract']:
                raise ValueError('Baseline failed original exact-contract resume')
            return validate_resume(meta,contract)
        trainer.complete_checkpoints,trainer.validate_resume=scan,validate
    def save_restored(model,optimizer,step):
        if mode!='continuous':return
        # Bypass the monitored save hook: no sampled optimizer update exists yet.
        wrapped=trainer.dcp.save;trainer.dcp.save=original_save
        try:
            save_state(model,optimizer,root/'restored_parent',rank,next(model.parameters()).device,
                dict(kind='horizon_restored_parent',step=step,world_size=8,diagnostic_only=True,
                     plan_id=spec['plan_id'],parent_contract_id=plan['parent_contract_id'],
                     child_contract_id=plan['child_contract_id']))
        finally:
            trainer.dcp.save=wrapped
    observer=NativeStateObserver(checked_path(plan['modes'][mode]['observations']),mode=mode,
        rank=rank,world=world,parent_step=plan['parent_step'],config=config,plan_id=spec['plan_id'],
        on_initial=save_restored if mode=='continuous' else None)
    try:
        with observe_native(trainer,observer):
            if mode=='baseline':
                from train.monitored import worker
            else:
                from train.monitored_horizon import worker
            worker(root,config_path,plan['modes'][mode]['attempt'])
    finally:
        trainer.dcp.load,trainer.dcp.save=original_load,original_save
        trainer.complete_checkpoints,trainer.validate_resume=original_scan,original_validate
        trainer.run=original_run


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec',required=True);parser.add_argument('--mode',choices=sorted(MODES),required=True)
    args=parser.parse_args()
    run(json.loads(resolve(args.spec).read_text()),args.mode)
