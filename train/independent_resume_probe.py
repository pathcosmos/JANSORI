"""Isolated own-NFS full-state restore/next-update/split-resume diagnostic.

No main launch, stop, pruning or source rename. Requires stopped-source identity
and unavailable old parent/local-proof paths before NCCL initialization.
"""
import argparse
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve,atomic_json,identity,sha256_file

MODES={'continuous':(0,2),'first':(0,1),'resumed':(1,2)}
OUTPUTS={'continuous':'continuous_final','first':'split_first','resumed':'resumed_final'}


def check_shared_binding(spec,read_bytes=None):
    """Small receipt binding check, usable for live and historical evidence."""
    import hashlib
    plan=spec['plan'];row=spec['shared_admission']
    path=resolve(row['path'])
    if (plan.get('operational_consumption_required') is not True or
            path!=resolve(plan['run_dir'])/'shared_admission.json'):
        raise ValueError('Shared admission diagnostic path/consumption differs')
    raw=path.read_bytes() if read_bytes is None else read_bytes(path)
    if len(raw)>16*2**20 or hashlib.sha256(raw).hexdigest()!=row['sha256']:
        raise ValueError('Shared admission diagnostic digest/size differs')
    record=json.loads(raw)
    if (record['purpose']!='shared_own_nfs_admission_v1' or
            record['binding']!=dict(purpose='independent_resume_probe',plan_id=identity(plan)) or
            record['production_adoption_authorized'] is not False or
            resolve(record['root'])!=resolve(plan['main_run']) or
            resolve(record['revision_path'])!=resolve(plan['revision']) or
            resolve(record['child'])!=resolve(spec['parent_checkpoint']) or
            record['child_stats']!=spec['parent_stats']):
        raise ValueError('Shared admission diagnostic source/binding differs')
    return record


def required_absent_paths(main,contract,revision):
    receipt=json.loads((main/'parent_handoff.json').read_text())
    prior=resolve(receipt['source_run'])
    old=json.loads((prior/'parent_handoff.json').read_text())
    paths={str(resolve(receipt['parent_checkpoint'])),str(resolve(old['parent_checkpoint'])),
           *[str(resolve(p)) for p in revision['local_proof_backups']]}
    for branch in contract.get('retention_branches',[]):
        for step in (8000,10000):
            if branch['first_step']<=step<=branch['last_step']:
                paths.add(str(resolve(branch['policy']['local_root'])/'checkpoints'/f'ckpt_{step:07d}'))
    return sorted(paths)


def check_spec(spec):
    from train.extended_handoff import validate_configuration
    from train.extended_horizon_worker_contract import bound_candidate
    from scripts.seal_horizon_handoff import require_parent_absent
    from scripts.tiered_checkpoint_retention import exact_path,inventory
    plan=spec['plan'];step=plan['parent_step']
    if (spec['plan_id']!=identity(plan) or plan['purpose']!='isolated_independent_nfs_resume_probe' or
            type(step) is not int or not 10000<step<=56500 or step%500 or
            plan['world_size']!=8 or plan['updates']!=2 or plan['main_target_modified'] is not False or
            plan['lr_policy']!='original_schedule_next_updates'):
        raise ValueError('Unreviewed independent diagnostic scope')
    main=exact_path(resolve(plan['main_run']));root=exact_path(resolve(plan['run_dir']))
    if root==main or root.is_relative_to(main) or main.is_relative_to(root) or not root.is_relative_to(resolve('runs')):
        raise ValueError('Independent diagnostic must not overlap source')
    for name,digest in {**plan['code'],**plan['inputs']}.items():
        if sha256_file(resolve(name))!=digest:raise ValueError('Independent diagnostic binding changed: '+name)
    required={'train/independent_resume_probe.py','train/independent_resume_bridge.py',
        'train/independent_child_resume.py','train/independent_checkpoint_handoff.py','scripts/audit_independent_training.py',
        'train/monitored_extended_horizon.py','scripts/run_independent_resume.py',
        'scripts/verify_independent_resume_probe.py','scripts/prepare_independent_resume.py',
        'scripts/independent_resume_process_handoff.py'}
    if not required<=set(plan['code']):raise ValueError('Independent diagnostic implementation missing')
    if plan.get('operational_consumption_required') is True and 'train/independent_operational_admission.py' not in plan['code']:
        raise ValueError('Operational consumption implementation missing')
    mc=json.loads((main/'contract.json').read_text());config=json.loads((main/'main.json').read_text())
    validate_configuration(config,mc)
    if mc['purpose']!='main_training' or mc['configurations']!={'main.json':sha256_file(main/'main.json')}:
        raise ValueError('Original main configuration changed')
    must_bind={main/'contract.json',main/'main.json',main/'training/training_contract.json',
               main/'parent_handoff.json',resolve(plan['revision'])}
    if not must_bind<={resolve(p) for p in plan['inputs']}:
        raise ValueError('Original diagnostic input bindings missing')
    require_parent_absent(plan['source_capture'],main)
    revision=json.loads(resolve(plan['revision']).read_text())
    prior=resolve(json.loads((main/'parent_handoff.json').read_text())['source_run'])
    if (prior/'parent_handoff.json' not in {resolve(p) for p in plan['inputs']} or
            plan['source_capture']!=json.loads(resolve(plan['source_capture_path']).read_text()) or
            resolve(plan['source_capture_path']) not in {resolve(p) for p in plan['inputs']}):
        raise ValueError('Original ancestry/process capture bindings missing')
    if (plan['max_seconds'],plan['max_pss_gib'],plan['local_budget_gib'],
            plan['durable_budget_gib'],plan['reserved_main_temp_gib'])!=(3600,256,192,1,1024):
        raise ValueError('Unreviewed independent diagnostic resource limits')
    from scripts.independent_resume_dependency_access import require_unavailable
    require_unavailable(plan,mc,revision)
    scratch=exact_path(Path(mc['tiered_retention']['temp_root'])/root.name)
    if Path(plan['scratch'])!=scratch or str(scratch.parent) not in mc['shared_storage']['local_temp_paths']:
        raise ValueError('Independent diagnostic scratch outside shared pool')
    receipt=json.loads((main/'parent_handoff.json').read_text())
    candidate=bound_candidate(receipt['parent_metadata'],receipt['original_contract'],receipt['execution_binding'])
    parent=json.loads((main/'training/training_contract.json').read_text())
    if (parent!=candidate or plan['parent_training_contract_id']!=parent['id'] or
            resolve(plan['parent_contract'])!=main/'training/training_contract.json' or
            resolve(plan['parent_config'])!=main/'main.json'):
        raise ValueError('Original training identity differs')
    checkpoint=main/'training'/f'ckpt_{step:07d}'
    if resolve(spec['parent_checkpoint'])!=checkpoint or resolve(revision['child']['checkpoint'])!=checkpoint:
        raise ValueError('Require exact admitted own NFS checkpoint')
    if resolve(spec['parent_proof'])!=main/'checkpoint_certificates'/(checkpoint.name+'.json'):
        raise ValueError('Own NFS certificate path differs')
    for name in ['parent_proof','audit']:
        if sha256_file(resolve(spec[name]))!=spec[name+'_sha256']:raise ValueError('Diagnostic proof changed')
    proof=json.loads(resolve(spec['parent_proof']).read_text())
    if revision['child']['step']!=step or revision['child']['certificate_sha256']!=spec['parent_proof_sha256']:
        raise ValueError('Independent revision certificate differs from execution source')
    if (proof['status']!='passed_main_checkpoint_storage' or proof['step']!=step or
            proof['training_contract_id']!=parent['id'] or resolve(proof['checkpoint'])!=checkpoint):
        raise ValueError('Own NFS certificate identity differs')
    inventory(checkpoint,proof)
    if file_stats(checkpoint)!=spec['parent_stats']:raise ValueError('Own source changed after controller SHA')
    from scripts.audit_main_training import verify_restart_metadata
    verify_restart_metadata(json.loads((checkpoint/'meta.json').read_text()),config,parent,step)
    audit=json.loads(resolve(spec['audit']).read_text());counts=audit['counts']
    if (audit['status']!='prepared_independent_prefix_audit_not_runtime_accepted' or
            resolve(audit['source_run'])!=main or audit['through_step']!=step or audit['target_steps']!=57000 or
            audit['validation_source'] or counts['committed_tokens']!=step*2097152 or
            counts['committed_step_rank_pairs']!=step*8 or len(counts['segments'])!=3 or
            counts['segments'][-1]['contract_id']!=parent['id'] or
            sum(counts['source_tokens'].values())!=step*2097152):
        raise ValueError('Actual independent prefix audit missing')
    segments=counts['segments']
    expected=[(receipt['original_contract']['id'],0,8000),
              (receipt['parent_metadata']['training_contract']['id'],8000,10000),
              (parent['id'],10000,step)]
    if [(s['contract_id'],s['start_step_exclusive'],s['through_step']) for s in segments]!=expected:
        raise ValueError('Independent original three-segment lineage differs')
    audit_implementation={'scripts/audit_independent_training.py','train/independent_checkpoint_handoff.py',
        'scripts/audit_extended_horizon.py','scripts/audit_horizon_training.py',
        'scripts/audit_main_training.py','scripts/extended_horizon_lineage.py'}
    audit_inputs={main/'contract.json',main/'main.json',main/'training/training_contract.json',
        main/'parent_handoff.json',main/'lineage/manifest.json',resolve(plan['revision']),
        resolve(spec['parent_proof']),checkpoint/'meta.json'}
    if (not audit_implementation<=set(audit['code']) or
            not audit_inputs<={resolve(p) for p in audit['evidence']} or
            resolve(audit['checkpoint_certificate'])!=resolve(spec['parent_proof'])):
        raise ValueError('Independent audit implementation/source evidence incomplete')
    for name,digest in {**audit['code'],**audit['evidence']}.items():
        if sha256_file(resolve(name))!=digest:raise ValueError('Underlying prefix evidence changed')
    if (spec['input_stats']!={p:stat_record(resolve(p)) for p in parent['files']} or
            json.loads(resolve(config['mix']).read_text())!=parent['mix']):
        raise ValueError('Fresh original input state changed')
    if plan.get('shared_admission_required') is True:check_shared_binding(spec)
    return plan,config,parent


def stat_record(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected regular immutable file')
    s = path.stat()
    return {k: getattr(s, k) for k in ['st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns']}


def file_stats(path):
    rows = {}
    for p in sorted(Path(path).rglob('*')):
        if p.is_symlink():
            raise ValueError('Checkpoint symlink rejected')
        if p.is_file():
            rows[str(p.relative_to(path))] = stat_record(p)
    if not rows:
        raise ValueError('Empty checkpoint')
    return rows


def diagnostic_lr(config,update_index):
    from train.train import lr_at
    return lr_at(update_index,config)


def verify_optimizer_step(optimizer, expected):
    import torch
    parameters = [p for group in optimizer.param_groups for p in group['params']]
    if not parameters or len(optimizer.state) != len(parameters):
        raise ValueError('Loaded optimizer state coverage differs')
    for parameter in parameters:
        state = optimizer.state[parameter]
        if set(state) != {'step', 'exp_avg', 'exp_avg_sq'}:
            raise ValueError('Loaded AdamW fields differ')
        step = state['step']
        if not isinstance(step, torch.Tensor) or step.numel() != 1 or step.item() != expected:
            raise ValueError('Loaded optimizer step differs')
        for key in ['exp_avg', 'exp_avg_sq']:
            if state[key].shape != parameter.shape or state[key].dtype != parameter.dtype:
                raise ValueError('Loaded optimizer tensor geometry differs')


def rng_state(device):
    import torch
    result = {'cpu': torch.get_rng_state()}
    if torch.device(device).type == 'cuda':
        result['cuda'] = torch.cuda.get_rng_state(device)
    return result


def restore_state(model, optimizer, checkpoint, rank, device, expected_step):
    import torch
    import torch.distributed.checkpoint as dcp
    from torch.distributed.checkpoint.state_dict import get_state_dict, set_state_dict
    from scripts.supervise_main_training import validate_optimizer_map
    path = Path(checkpoint)
    if not (path/'.complete').is_file():
        raise ValueError('Incomplete source checkpoint')
    validate_optimizer_map(dcp.FileSystemReader(path).read_metadata())
    msd, osd = get_state_dict(model, optimizer)
    state = {'model': msd, 'optim': osd}
    dcp.load(state, checkpoint_id=path)
    set_state_dict(model, optimizer, model_state_dict=state['model'], optim_state_dict=state['optim'])
    verify_optimizer_step(optimizer, expected_step)
    rng = torch.load(path/f'rng_rank{rank}.pt', map_location='cpu', weights_only=True)
    keys = {'cpu', 'cuda'} if torch.device(device).type == 'cuda' else {'cpu'}
    if set(rng) != keys or any(not isinstance(v, torch.Tensor) or v.dtype != torch.uint8 or v.ndim != 1 for v in rng.values()):
        raise ValueError('Invalid rank RNG state')
    torch.set_rng_state(rng['cpu'])
    if 'cuda' in rng:
        torch.cuda.set_rng_state(rng['cuda'], device)
    if any(not torch.equal(v, rng_state(device)[k]) for k, v in rng.items()):
        raise ValueError('Rank RNG failed exact restore')


def save_state(model, optimizer, path, rank, device, metadata):
    import torch
    import torch.distributed as dist
    import torch.distributed.checkpoint as dcp
    from torch.distributed.checkpoint.state_dict import get_state_dict
    path = Path(path)
    if rank == 0:
        path.mkdir(parents=True, exist_ok=False)  # No overwrite or implicit cleanup.
    dist.barrier()
    msd, osd = get_state_dict(model, optimizer)
    dcp.save({'model': msd, 'optim': osd}, checkpoint_id=path)
    torch.save(rng_state(device), path/f'rng_rank{rank}.pt')
    dist.barrier()
    if rank == 0:
        atomic_json(path/'meta.json', metadata)
        (path/'.complete').touch()
    dist.barrier()
    # .complete is only a candidate; controller fresh SHA/fsync certification
    # and independent state comparisons are required for final acceptance.


def restore_own_source(spec,config,model,optimizer,source,rank,device):
    from scripts.independent_resume_dependency_access import storage_view
    with storage_view(spec['plan']):
        return _restore_own_source(spec,config,model,optimizer,source,rank,device)


def _restore_own_source(spec,config,model,optimizer,source,rank,device):
    from train.independent_resume_bridge import PreparedIndependentAdmission
    import torch.distributed.checkpoint as dcp
    plan=spec['plan']
    if plan.get('operational_consumption_required') is True:
        from train.independent_operational_admission import PreparedOperationalAdmission
        if plan.get('shared_admission_required') is True:
            check_shared_binding(spec)
            shared=spec['shared_admission']
            admission=PreparedOperationalAdmission.from_shared(shared['path'],shared['sha256'],rank=rank,
                binding=dict(purpose='independent_resume_probe',plan_id=identity(plan)))
        else:
            admission=PreparedOperationalAdmission(resolve(plan['main_run']),resolve(plan['revision']),rank=rank)
        consumed=[]
        with admission.operational_hooks(consumed.append) as module:
            receipt,candidate,parent_proof=module.load_handoff(resolve(plan['main_run']),admission.contract,admission.config)
            adapter=module.parent_resume(receipt,candidate,parent_proof,admission.config,admission.contract)
            if adapter.checkpoints(resolve(config['out_dir']))[-1]!=source:
                raise ValueError('Operational adapter selected a different own source')
            adapter.validate(json.loads((source/'meta.json').read_text()),candidate)
            original_load=dcp.load
            dcp.load=lambda *args,**kwargs:adapter.load(original_load,*args,**kwargs)
            try:restore_state(model,optimizer,source,rank,device,plan['parent_step'])
            finally:dcp.load=original_load
        if len(consumed)!=1:raise ValueError('Exactly one full own-state consumption receipt required')
        return consumed[0]
    admission=PreparedIndependentAdmission(resolve(plan['main_run']),resolve(plan['revision']))
    receipt,candidate,parent_proof=admission.handoff(resolve(plan['main_run']),admission.contract,admission.config)
    adapter=admission.resume(receipt,candidate,parent_proof,admission.config,admission.contract)
    if adapter.checkpoints(resolve(config['out_dir']))[-1]!=source:
        raise ValueError('Independent adapter selected a different own source')
    adapter.validate(json.loads((source/'meta.json').read_text()),candidate)
    original_load=dcp.load
    dcp.load=lambda *args,**kwargs:adapter.load(original_load,*args,**kwargs)
    try:restore_state(model,optimizer,source,rank,device,plan['parent_step'])
    finally:dcp.load=original_load
    admission.check()


def run(spec, mode):
    import torch
    import torch.distributed as dist
    from torch.distributed.fsdp import fully_shard, MixedPrecisionPolicy
    from model.model import Transformer, get_config
    from train.data import MixSampler
    from train.rehearsal import SourceLedger, observed_sampler
    from train.runtime import require_finite, ensure_finite_gradients

    plan, config, parent = check_spec(spec)
    if plan['framework'] != {'torch': torch.__version__, 'cuda': torch.version.cuda}:
        raise ValueError('Diagnostic framework differs from parent')
    if mode not in MODES or dist.get_world_size() != 8 or dist.get_backend() != 'nccl':
        raise ValueError('Probe requires a reviewed mode and actual8GPU ranks')
    rank = dist.get_rank()
    local = int(os.environ['LOCAL_RANK'])
    torch.cuda.set_device(local)
    device = torch.device('cuda', local)
    torch.backends.cuda.matmul.allow_tf32 = True
    root, scratch = resolve(plan['run_dir']), Path(plan['scratch'])
    if local != rank or 'H200' not in torch.cuda.get_device_name(local):
        raise ValueError('Expected actual8rank H200 device mapping')
    runtime_path = root/'runtime'/f'{mode}-rank{rank}.json'
    if runtime_path.exists():
        raise ValueError('Preserve prior diagnostic runtime receipt')
    stat = Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')', 1)[1].split()
    prop = torch.cuda.get_device_properties(local)
    if torch.cuda.device_count() != 8:
        raise ValueError('Expected exactly8 visible H200 GPUs')
    from scripts.horizon_probe_process import OWNER_ENV
    if not os.environ.get(OWNER_ENV):
        raise ValueError('Supervised process ownership tag missing')
    atomic_json(runtime_path, dict(owner=os.environ[OWNER_ENV], plan_id=spec['plan_id'], spec_id=identity(spec),
        mode=mode, rank=rank, local_rank=local, world_size=dist.get_world_size(),
        backend=str(dist.get_backend()), device_name=prop.name,
        device_total_memory=prop.total_memory,
        device_uuid=str(prop.uuid) if hasattr(prop, 'uuid') else None,
        framework=plan['framework'], pid=os.getpid(), parent_pid=os.getppid(),
        start_ticks=int(stat[19]), diagnostic_only=True, actual8gpu_acceptance=False))
    begin, end = MODES[mode]
    source = resolve(spec['parent_checkpoint']) if begin == 0 else scratch/'split_first'
    if begin:
        from scripts.verify_final_resume_probe import verify_inventory
        proof = json.loads((root/'certificates/split_first.json').read_text())
        verify_inventory(source, proof)
        meta = json.loads((source/'meta.json').read_text())
        if meta != metadata_for(spec, 1):
            raise ValueError('Intermediate diagnostic resume identity differs')
    # Every mode has its own process. No incomplete prior attempt is overwritten.
    destinations = [scratch/OUTPUTS[mode]]
    if mode == 'continuous':
        destinations.append(scratch/'restored_parent')
    if any(p.exists() for p in destinations) or (root/'ledgers'/f'{mode}-rank{rank}.json').exists():
        raise ValueError('Existing diagnostic attempt is preserved; use reviewed recovery')
    cfg = get_config(config['model'], vocab_size=config['vocab_size'], max_seq_len=config['seq_len'])
    with torch.device('meta'):
        model = Transformer(cfg)
    policy = MixedPrecisionPolicy(param_dtype=torch.bfloat16, reduce_dtype=torch.float32)
    for block in model.blocks:
        fully_shard(block, mp_policy=policy)
    fully_shard(model, mp_policy=policy)
    model.to_empty(device=device)
    torch.manual_seed(config['seed']+rank)
    with torch.no_grad():
        model.init_weights()
    decay = [p for n, p in model.named_parameters() if p.ndim >= 2]
    nodecay = [p for n, p in model.named_parameters() if p.ndim < 2]
    optimizer = torch.optim.AdamW([{'params': decay, 'weight_decay': config['wd']},
                                  {'params': nodecay, 'weight_decay': 0.0}], lr=config['lr'],
                                 betas=(config['beta1'], config['beta2']), fused=True)
    selected_stats = spec['parent_stats'] if begin == 0 else proof['stats']
    if file_stats(source) != selected_stats:
        raise ValueError('Selected source changed before full-state load')
    if begin==0:
        consumption=restore_own_source(spec,config,model,optimizer,source,rank,device)
        if plan.get('operational_consumption_required') is True:
            runtime=json.loads(runtime_path.read_text());runtime['own_state_consumption']=consumption
            atomic_json(runtime_path,runtime)
    else:
        restore_state(model,optimizer,source,rank,device,plan['parent_step']+begin)
    if file_stats(source) != selected_stats:
        raise ValueError('Selected source changed during full-state load')
    if mode == 'continuous':
        save_state(model, optimizer, scratch/'restored_parent', rank, device, metadata_for(spec, 0))
    ledger = SourceLedger(parent['source_order'])
    sampler = observed_sampler(MixSampler, ledger)(config['mix'], config['seq_len'], config['seed'], version=config['sampler_version'])
    micro_tokens = config['micro_bsz']*config['seq_len']*8
    if config['global_tokens'] % micro_tokens:
        raise ValueError('Nonintegral accumulation')
    accum = config['global_tokens']//micro_tokens
    records = []
    def persist(success):
        atomic_json(root/'ledgers'/f'{mode}-rank{rank}.json', dict(rank=rank, mode=mode, success=success,
                    plan_id=spec['plan_id'], steps=ledger.steps, metrics=records,
                    parent_step=plan['parent_step'], diagnostic_only=True))
    model.train()
    persist(False)
    for offset in range(begin, end):
        step = plan['parent_step']+offset
        lr = diagnostic_lr(config,step)
        start = time.monotonic()
        for group in optimizer.param_groups:
            group['lr'] = lr
        loss_acc = torch.zeros((), device=device)
        for micro in range(accum):
            x, y = sampler.batch(step, rank, micro, config['micro_bsz'])
            x, y = x.to(device, non_blocking=True), y.to(device, non_blocking=True)
            model.set_requires_gradient_sync(micro == accum-1)
            model.set_requires_all_reduce(micro == accum-1)
            with torch.autocast('cuda', dtype=torch.bfloat16):
                loss = model(x, y)/accum
            require_finite(loss, 'diagnostic loss', device)
            loss.backward()
            loss_acc += loss.detach()
        ensure_finite_gradients(model, device)
        norm = torch.nn.utils.clip_grad_norm_(model.parameters(), config['clip'])
        require_finite(norm, 'diagnostic gradient norm', device)
        optimizer.step()
        ledger.commit()
        optimizer.zero_grad(set_to_none=True)
        verify_optimizer_step(optimizer, step+1)
        dist.all_reduce(loss_acc, op=dist.ReduceOp.AVG)
        torch.cuda.synchronize()
        records.append(dict(step=step+1, loss=loss_acc.item(), gnorm=norm.item(), lr=lr,
                            elapsed_seconds=time.monotonic()-start))
        persist(False)
    save_state(model, optimizer, scratch/OUTPUTS[mode], rank, device, metadata_for(spec, end))
    # Preserve the final source and inputs across all modes, including save I/O.
    if (file_stats(resolve(spec['parent_checkpoint'])) != spec['parent_stats'] or
            spec['input_stats'] != {p: stat_record(resolve(p)) for p in parent['files']}):
        raise ValueError('Final parent/input changed during isolated resume')
    persist(True)


def metadata_for(spec, offset):
    p = spec['plan']
    return dict(kind='independent_resume_diagnostic', step=p['parent_step']+offset, diagnostic_updates=offset,
                world_size=p['world_size'], diagnostic_contract_id=spec['plan_id'],
                parent_step=p['parent_step'], parent_checkpoint=spec['parent_checkpoint'],
                parent_training_contract_id=p['parent_training_contract_id'],
                parent_config_sha256=p['inputs'][p['parent_config']], main_target_modified=False,
                lr_policy=p['lr_policy'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', required=True)
    parser.add_argument('--mode', choices=sorted(MODES), required=True)
    args = parser.parse_args()
    supplied=json.loads(resolve(args.spec).read_text())
    check_spec(supplied)  # Refuse live/unisolated source before NCCL initialization.
    from train.deterministic_train import configure
    configure()
    import torch.distributed as dist
    dist.init_process_group('nccl', timeout=timedelta(minutes=5))
    try:
        run(supplied,args.mode)
    finally:
        dist.destroy_process_group()
