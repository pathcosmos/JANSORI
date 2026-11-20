"""Wait for audited final3B, then supervise isolated8GPU resume verification.

The parent model/config/run is read-only. Preparation and wait states are not
final acceptance. Failed artifacts remain for review; no automatic retry,
pruning, external upload or main-training restart is implemented here.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import PROJECT_ROOT
from common.data_contracts import resolve, artifact_path, atomic_json, sha256_file, identity
from train.final_horizon_resume_probe import (check_spec, file_stats, stat_record, metadata_for,
                                     OUTPUTS, MODES, verify_parent_metadata)
from scripts.evaluate_final_horizon import main_observation, completed_main
from scripts.evaluate_final_rehearsal import acquire_idle_gpu, check_bindings
from scripts.tiered_checkpoint_retention import size_tree, inventory

from train.horizon_target import TARGET_STEPS

GIB = 2**30


def load_plan(root):
    root = resolve(root)
    plan = json.loads((root/'plan.json').read_text())
    marker = json.loads((root/'prepared.json').read_text())
    if (marker['plan_sha256'] != sha256_file(root/'plan.json') or marker['plan_id'] != identity(plan) or
            resolve(plan['run_dir']) != root or plan['purpose'] != 'isolated_final_horizon_resume_probe' or
            plan['parent_step'] != TARGET_STEPS or plan['world_size'] != 8 or plan['updates'] != 2 or
            plan['main_target_modified'] is not False or plan['lr_policy'] != 'freeze_last_parent_update'):
        raise ValueError('Unreviewed isolated final resume plan')
    if (plan['max_seconds'],plan['max_pss_gib'],plan['local_budget_gib'],
            plan['durable_budget_gib'],plan['reserved_main_temp_gib']) != (3600,256,192,1,1024):
        raise ValueError('Unreviewed final resume execution limits')
    check_bindings(plan)
    main = resolve(plan['main_run'])
    mc = json.loads((main/'contract.json').read_text())
    if (root.is_relative_to(main) or main.is_relative_to(root) or not root.is_relative_to(resolve('runs')) or
            Path(plan['scratch']) != Path(mc['tiered_retention']['temp_root'])/root.name or
            any(p.is_symlink() for path in [root, Path(plan['scratch'])] for p in [path, *path.parents])):
        raise ValueError('Diagnostic run/scratch path differs or overlaps source')
    return plan


def make_contract(root):
    root = resolve(root); plan = load_plan(root)
    added = ['scripts/run_final_horizon_resume.py', 'scripts/evaluate_final_horizon.py',
             'scripts/evaluate_final_rehearsal.py', 'scripts/process_accounting.py',
             'scripts/resource_admission.py', 'scripts/hardware_profile.py',
             'scripts/horizon_storage_plan.py', 'scripts/horizon_probe_process.py',
             'scripts/horizon_process_handoff.py']
    return dict(version=1, purpose='final_horizon_resume_controller', plan_sha256=sha256_file(root/'plan.json'),
                code={**plan['code'], **{p:sha256_file(resolve(p)) for p in added}},
                inputs={**plan['inputs'], artifact_path(root/'plan.json'):sha256_file(root/'plan.json'),
                        artifact_path(root/'prepared.json'):sha256_file(root/'prepared.json')},
                poll_seconds=30, wait_max_seconds=22*86400, worker_max_seconds=plan['max_seconds'],
                worker_pss_gib=plan['max_pss_gib'], local_budget_gib=plan['local_budget_gib'],
                durable_budget_gib=plan['durable_budget_gib'], main_run=plan['main_run'],
                scope='Actual35000 audit, fresh parent SHA/input hashes,8GPU global lock, three isolated processes and full-state comparisons. No main update or pruning.')


def owned_processes(root):
    """Find live exact worker/spec argument handles, including orphan torchranks."""
    root = resolve(root); found = []
    for directory in Path('/proc').glob('[0-9]*'):
        try:
            if (directory/'stat').read_text().split(') ', 1)[1].split()[0] == 'Z':
                continue
            args = (directory/'cmdline').read_bytes().decode(errors='replace').split('\0')
            owns = False
            if any('train.final_horizon_resume_probe' == arg for arg in args) and '--spec' in args:
                owns = resolve(args[args.index('--spec')+1]) == root/'spec.json'
            if '--worker' in args and '--run-dir' in args and any(arg.endswith('run_final_horizon_resume.py') for arg in args):
                owns |= resolve(args[args.index('--run-dir')+1]) == root
            if owns:
                found.append(int(directory.name))
        except (OSError, ValueError, IndexError):
            continue
    return sorted(found)


def reject_previous_execution(root):
    root = resolve(root)
    processes = owned_processes(root)
    if processes:
        raise RuntimeError('Existing actual diagnostic process handles: '+str(processes))
    plan = load_plan(root)
    if ((root/'supervision').exists() or (root/'runtime').exists() or (root/'execution_started.json').exists() or (root/'spec.json').exists() or
            (root/'worker_state.json').exists() or (root/'ledgers').exists() or Path(plan['scratch']).exists()):
        raise RuntimeError('Preserve previous diagnostic attempt; review before using a new execution namespace')


def admission(root, plan):
    from scripts.resource_admission import inspect
    from scripts.horizon_storage_plan import enforce_runtime_space
    from scripts.tiered_checkpoint_retention import exact_path
    root=exact_path(resolve(root));hw=inspect();main=exact_path(resolve(plan['main_run']))
    mc=json.loads((main/'contract.json').read_text());policy=mc['tiered_retention']
    native=exact_path(resolve(plan['native_run']));nc=json.loads((native/'contract.json').read_text())
    if (plan['durable_budget_gib'],plan['local_budget_gib'],plan['max_pss_gib'],plan['reserved_main_temp_gib'])!=(1,192,256,1024):
        raise ValueError('Unreviewed final resume resource limits')
    if (nc['main_run']!=plan['main_run'] or nc.get('final_step')!=TARGET_STEPS or
            (nc['host_rss_gib'],nc['durable_budget_gib'],nc['local_budget_gib'])!=(256,8,8)):
        raise ValueError('Concurrent native target/reservation differs')
    combined=mc['max_pss_gib']+nc['host_rss_gib']+plan['max_pss_gib']
    if (hw['gpus']!=mc['gpu_identity'] or len(hw['gpus'])!=8 or
            hw['memory']['cache_aware_admission']['headroom_gib']<combined):
        raise RuntimeError('Combined main/native/resume memory or8GPU admission unavailable')
    for path,gib in [(root,1),(native,8)]:
        if mc['space_reservations']['nfs'].get(str(path))!=gib*GIB:
            raise ValueError('Exact combined final NFS reservation missing')
    shared=enforce_runtime_space(main,mc)
    temp=exact_path(Path(policy['temp_root']));scratch=exact_path(Path(plan['scratch']))
    if (scratch!=temp/root.name or temp.parent.stat().st_dev!=hw['storage']['scratch_parent']['device'] or
            temp.parent.stat().st_dev==root.stat().st_dev):
        raise ValueError('Reviewed temporary storage is not the separate local filesystem')
    own=size_tree(root);used=size_tree(scratch);native_used=size_tree(temp/'final_native')
    total=shared['local_temporary_used_bytes']
    if total<used+native_used:raise ValueError('Scratch usage is outside combined temporary accounting')
    projected=total-used-native_used+192*GIB+8*GIB
    if own>GIB or used>192*GIB or native_used>8*GIB or projected>1024*GIB:
        raise RuntimeError('Final resume shared temporary/durable cap exceeded')
    return hw,dict(**shared,projected_temporary_bytes=projected,combined_memory_gib=combined,
        own_durable_bytes=own,own_local_bytes=used,
        scope='Exact existing NFS reservations reused; local192/native8 are inside shared1024GiB.')


def fresh_inputs(plan, output):
    from train.parallel_contract import training_contract
    parent = json.loads(resolve(plan['parent_contract']).read_text())
    config = json.loads(resolve(plan['parent_config']).read_text())
    before = {p:stat_record(resolve(p)) for p in parent['files']}
    fresh = training_contract(config, 8, config['holdout'], workers=32,
                expected_files=parent['files'], observe=lambda metrics:atomic_json(output, metrics))
    # Rebuild the same extension that the actual horizon worker attaches to
    # fresh native inputs. Do not copy a parent ID over mismatching inputs.
    main=resolve(plan['main_run']);mc=json.loads((main/'contract.json').read_text())
    fresh.pop('id')
    fresh['monitored_evaluation']=dict(probe_sha256=sha256_file(main/'probe/probe.json'),
        prompts_sha256=sha256_file(resolve(mc['prompts'])),generation_steps=mc['generation_steps'],
        code_sha256=sha256_file(resolve('train/monitored_horizon.py')),
        source_hooks_sha256=sha256_file(resolve('train/rehearsal.py')))
    fresh['horizon_transition']=mc['horizon_execution']
    fresh['id']=identity(fresh)
    if fresh!=parent:
        raise ValueError('Fresh35k input/config/code/lineage identity differs')
    after = {p:stat_record(resolve(p)) for p in parent['files']}
    if before != after:
        raise ValueError('Training input changed across live fresh verification')
    return after


def materialize_spec(root, plan):
    checkpoint, proof, meta, audit = completed_main(plan['main_run'])
    parent = json.loads(resolve(plan['parent_contract']).read_text())
    config = json.loads(resolve(plan['parent_config']).read_text())
    verify_parent_metadata(meta, config, parent, plan['parent_training_contract_id'])
    if (root/'spec.json').exists():
        raise ValueError('Existing final execution spec is preserved')
    before = file_stats(checkpoint)
    inventory(checkpoint, proof, hashes=True, workers=8)
    if before != file_stats(checkpoint):
        raise ValueError('Final parent changed during fresh SHA verification')
    stats = fresh_inputs(plan, root/'fresh_input_hash.json')
    atomic_json(root/'final_training_audit.json', audit)
    proof_path = resolve(audit['checkpoint_certificate'])
    spec = dict(plan=plan, plan_id=identity(plan), parent_checkpoint=artifact_path(checkpoint),
                parent_proof=artifact_path(proof_path), parent_proof_sha256=sha256_file(proof_path),
                audit=artifact_path(root/'final_training_audit.json'), audit_sha256=sha256_file(root/'final_training_audit.json'),
                parent_stats=before, input_stats=stats)
    check_spec(spec)
    atomic_json(root/'spec.json', spec)
    return spec


def require_comparison(result):
    if (result.get('exact') is not True or result.get('tensors') != 1020 or result.get('model_tensors') != 255 or
            result.get('optimizer_tensors') != 765 or result.get('rng_ranks') != 8):
        raise ValueError('Actual3B1020tensor/8rank comparison is incomplete')


def verify_runtime(root, spec, *, snapshot=None):
    """Bind all24 real NCCL receipts to the supervised launcher identities."""
    from scripts.audit_main_training import Snapshot
    snap=snapshot if snapshot is not None else Snapshot()
    root=Path(root);plan=spec['plan']
    handle_path=root/'supervision/handle.json';events_path=root/'supervision/events.jsonl'
    handle=snap.read(handle_path);owner=handle['owner']
    if not isinstance(owner,str) or len(owner)!=32:
        raise ValueError('Invalid supervised ownership token')
    expected_tail=[str(Path(__file__).resolve()),'--run-dir',str(root),'--worker']
    if handle['command'][-4:]!=expected_tail:
        raise ValueError('Supervised final worker command differs')
    rows=snap.read(events_path,lines=True,require_newline=True)
    bound=set()
    for event in rows:
        if event['event']=='process_bound':bound.add((event['pid'],event['start_ticks']))
    if (handle['process']['pid'],handle['process']['start_ticks']) not in bound:
        raise ValueError('Supervised worker identity not pinned')
    wanted={f'{m}-rank{r}.json' for m in MODES for r in range(8)}
    paths=sorted((root/'runtime').glob('*.json'))
    if {p.name for p in paths}!=wanted:raise ValueError('Actual24rank runtime coverage missing')
    evidence=[handle_path,events_path,*paths];seen=set();devices={}
    for mode in MODES:
        hp=root/'attempts'/(mode+'.handle.json');rp=root/'attempts'/(mode+'.result.json')
        h=snap.read(hp);r=snap.read(rp);launcher=h['process']
        command=[sys.executable,'-m','torch.distributed.run','--standalone','--nproc_per_node=8',
            '--module','train.final_horizon_resume_probe','--spec',str(root/'spec.json'),'--mode',mode]
        if (h['command']!=command or launcher['argv']!=command or h['owner']!=owner or r['owner']!=owner or
                r['process']!=launcher or r['exit_code']!=0 or launcher['ppid']!=handle['pid'] or
                (launcher['pid'],launcher['start_ticks']) not in bound):
            raise ValueError('Actual final launcher identity/completion differs')
        evidence.extend([hp,rp])
        for rank in range(8):
            row=snap.read(root/'runtime'/f'{mode}-rank{rank}.json')
            stamp=(row['pid'],row['start_ticks'])
            if (row['plan_id']!=identity(plan) or row['spec_id']!=identity(spec) or row['mode']!=mode or
                    row['rank']!=rank or row['local_rank']!=rank or row['world_size']!=8 or row['backend']!='nccl' or
                    row['framework']!=plan['framework'] or row['parent_pid']!=launcher['pid'] or
                    row['owner']!=owner or row['diagnostic_only'] is not True or
                    row['actual8gpu_acceptance'] is not False or 'H200' not in row['device_name'] or
                    type(row['device_total_memory']) is not int or row['device_total_memory']<130*GIB or
                    type(row['pid']) is not int or type(row['start_ticks']) is not int or
                    row['pid']<2 or row['start_ticks']<1 or stamp in seen or stamp not in bound):
                raise ValueError('Actual8GPU rank provenance differs')
            seen.add(stamp)
            device=(row['device_name'],row['device_total_memory'],row['device_uuid'])
            if rank in devices and devices[rank]!=device:raise ValueError('Device identity changed across modes')
            devices[rank]=device
    uuids=[d[2] for d in devices.values()]
    if any(u is None for u in uuids) or len(set(uuids))!=8:
        raise ValueError('Distinct actual8GPU device UUIDs missing')
    snap.unchanged()
    return evidence


def collect_result(root, spec):
    from scripts.verify_final_resume_probe import verify_inventory, ledger_review
    from scripts.audit_main_training import Snapshot
    snap=Snapshot()
    if snap.read(root/'spec.json')!=spec:raise ValueError('Final diagnostic spec changed')
    plan, config, parent = check_spec(spec)
    scratch = Path(plan['scratch'])
    paths = sorted((root/'ledgers').glob('*.json'))
    wanted = {f'{m}-rank{r}.json' for m in MODES for r in range(8)}
    if {p.name for p in paths} != wanted:
        raise ValueError('Diagnostic24 ledger file coverage differs')
    counts = ledger_review([snap.read(p) for p in paths], parent_step=TARGET_STEPS, world=8,
        config=config, source_order=parent['source_order'], plan_id=identity(plan))
    checks = {name:snap.read(root/(name+'.json')) for name in ['roundtrip_comparison','resume_comparison']}
    for result in checks.values():
        require_comparison(result)
    outputs = {'restored_parent':0, 'continuous_final':2, 'split_first':1, 'resumed_final':2}
    if {p.name for p in scratch.iterdir()} != set(outputs):
        raise ValueError('Unexpected diagnostic residue or missing checkpoint')
    for name, offset in outputs.items():
        proof = snap.read(root/'certificates'/(name+'.json'))
        if proof['metadata'] != metadata_for(spec, offset) or resolve(proof['path'])!=scratch/name:
            raise ValueError('Diagnostic certificate lineage differs')
        verify_inventory(scratch/name, proof)
    runtime_evidence = verify_runtime(root, spec, snapshot=snap)
    proof_paths = [*runtime_evidence, root/'spec.json', root/'final_training_audit.json', root/'fresh_input_hash.json',
                   resolve(spec['parent_proof']), *paths, *sorted((root/'certificates').glob('*.json')),
                   root/'roundtrip_comparison.json', root/'resume_comparison.json', root/'execution_timing.json']
    for p in proof_paths:
        if artifact_path(p) not in snap.bindings:snap.read(p)
    result = dict(status='passed_actual_final3b_8gpu_resume', parent_checkpoint=spec['parent_checkpoint'],
        parent_step=TARGET_STEPS, target_steps=TARGET_STEPS, main_target_modified=False, counts=counts, comparisons=checks,
        local_checkpoint_bytes=size_tree(scratch), local_checkpoint_paths=[str(scratch/name) for name in outputs],
        evidence=dict(snap.bindings),
        scope='Actual final3B full model/AdamW/8rank RNG roundtrip and continuous2 versus split1/restart1 exact. Diagnostic tokens excluded from main; native quality, official benchmarks and retention audit remain separate.')
    if file_stats(resolve(spec['parent_checkpoint'])) != spec['parent_stats']:
        raise ValueError('Final parent changed during result collection')
    if sorted((root/'ledgers').glob('*.json'))!=paths:raise ValueError('Final ledger inventory changed')
    for name in outputs:
        proof=snap.read(root/'certificates'/(name+'.json'))
        verify_inventory(scratch/name,proof)
    snap.unchanged()
    return result



def worker(root):
    import torch
    from scripts.verify_final_resume_probe import seal, compare_states
    root = resolve(root); contract = json.loads((root/'contract.json').read_text())
    check_bindings(contract); plan = load_plan(root)
    if plan['framework'] != dict(torch=str(torch.__version__), cuda=torch.version.cuda):
        raise ValueError('Final probe framework changed')
    from scripts.horizon_probe_process import OWNER_ENV
    execution=json.loads((root/'execution_started.json').read_text())
    # The owner writes its pidfd receipt immediately after Popen. A fast child
    # may reach this point before NFS publication completes; wait for that exact
    # receipt without launching or retrying anything.
    handle_path=root/'supervision/handle.json';receipt_deadline=time.monotonic()+30
    if not os.environ.get(OWNER_ENV):raise ValueError('Supervised ownership tag missing')
    while not handle_path.exists():
        if time.monotonic()>=receipt_deadline:raise RuntimeError('Supervised identity publication timed out')
        time.sleep(.05)
    handle=json.loads(handle_path.read_text())
    stamp=Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')',1)[1].split()
    if (execution['plan_id']!=identity(plan) or handle['pid']!=os.getpid() or
            handle['process']['start_ticks']!=int(stamp[19]) or
            not os.environ.get(OWNER_ENV) or handle['owner']!=os.environ[OWNER_ENV]):
        raise ValueError('Supervised execution identity differs')
    scratch = Path(plan['scratch']); started = time.monotonic(); phases = []
    def notify(case, **extra):
        atomic_json(root/'worker_state.json', dict(phase='running', case=case, pid=os.getpid(),
                    elapsed_seconds=time.monotonic()-started, **extra))
    # CPU-only audit and fresh SHA can overlap native GPU evaluation. The
    # parent is already final and immutable; recheck its snapshots at GPU entry.
    notify('audit_fresh_parent_and_inputs')
    hw, budgets = admission(root, plan)
    atomic_json(root/'input_hardware.json', hw); atomic_json(root/'input_admission.json', budgets)
    spec = materialize_spec(root, plan)
    notify('await_idle8gpu')
    with acquire_idle_gpu(notify):
        # Recheck measured reservations after any GPU lock wait.
        hw, budgets = admission(root, plan)
        atomic_json(root/'execution_hardware.json', hw); atomic_json(root/'execution_admission.json', budgets)
        check_spec(spec)
        scratch.mkdir(parents=True, exist_ok=False)
        for name in ['ledgers','certificates','attempts','runtime']:
            (root/name).mkdir(exist_ok=False)
        for mode in ['continuous', 'first', 'resumed']:
            check_bindings(contract); check_spec(spec)
            notify('actual8gpu_'+mode)
            command = [sys.executable,'-m','torch.distributed.run','--standalone','--nproc_per_node=8',
                       '--module','train.final_horizon_resume_probe','--spec',str(root/'spec.json'),'--mode',mode]
            begin = time.monotonic()
            # The outer pidfd owner tag is inherited by launcher and ranks,
            # including ranks that create their own sessions. No nested owner
            # replaces the tag, so a failed worker cannot strand GPU children.
            from scripts.horizon_process_handoff import process_record
            from scripts.horizon_probe_process import OWNER_ENV
            owner=os.environ.get(OWNER_ENV)
            if not owner:raise ValueError('Supervised process ownership tag missing')
            with (root/'attempts'/(mode+'.log')).open('x') as stream:
                proc=subprocess.Popen(command,cwd=PROJECT_ROOT,stdout=stream,stderr=subprocess.STDOUT)
            handle=process_record(proc.pid)
            atomic_json(root/'attempts'/(mode+'.handle.json'),dict(process=handle,command=command,owner=owner))
            notify('actual8gpu_'+mode,torchrun_pid=proc.pid)
            rc=proc.wait()
            atomic_json(root/'attempts'/(mode+'.result.json'),dict(exit_code=rc,process=handle,owner=owner))
            if rc:raise RuntimeError('Actual8GPU diagnostic failed in '+mode+'; preserve outputs')
            targets = [(OUTPUTS[mode], MODES[mode][1])]
            if mode == 'continuous':
                targets.insert(0, ('restored_parent',0))
            notify('certify_'+mode)
            for name, offset in targets:
                seal(scratch/name, metadata_for(spec,offset), root/'certificates'/(name+'.json'))
            phases.append(dict(mode=mode, elapsed_seconds=time.monotonic()-begin))
            check_spec(spec)
    # CPU comparisons can overlap final native scoring after releasing the GPUs.
    torch.set_num_threads(8)
    for label, left, right in [('roundtrip_comparison',resolve(spec['parent_checkpoint']),scratch/'restored_parent'),
                              ('resume_comparison',scratch/'continuous_final',scratch/'resumed_final')]:
        notify(label)
        result = compare_states(left,right,world=8); require_comparison(result)
        atomic_json(root/(label+'.json'),result)
    atomic_json(root/'execution_timing.json', dict(phases=phases, elapsed_seconds=time.monotonic()-started))
    check_bindings(contract)
    report = collect_result(root,spec)
    atomic_json(root/'worker_result.json',report)
    notify('worker_verified_pending_supervisor')


def supervise_command(command, *, root, env, guard, interval=2):
    from scripts.horizon_probe_process import OwnedCommand
    root=Path(root);directory=root/'supervision'
    directory.mkdir(exist_ok=False)
    owned=OwnedCommand(command,directory,env)
    def checked(owner,elapsed,peak):
        guard(owner.proc.pid,elapsed,peak)
    return owned.run(checked,interval=interval)


def verify_completed(root, contract):
    root = Path(root); check_bindings(contract)
    marker = json.loads((root/'.done').read_text())
    if marker != dict(report_sha256=sha256_file(root/'report.json'),contract_sha256=sha256_file(root/'contract.json')):
        raise ValueError('Final resume completion proof differs')
    report = json.loads((root/'report.json').read_text())
    fresh = collect_result(root,json.loads((root/'spec.json').read_text()))
    if any(report.get(k) != v for k,v in fresh.items()):
        raise ValueError('Final resume report differs from current content proof')
    return report


def main(args):
    root = resolve(args.run_dir)
    if args.worker:
        return worker(root)
    plan = load_plan(root)
    with (root/'controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        contract = make_contract(root); cp = root/'contract.json'
        if cp.exists() and json.loads(cp.read_text()) != contract:
            raise ValueError('Controller binding changed; preserve old run and prepare a new namespace')
        if not cp.exists():
            atomic_json(cp,contract)
        if args.prepare:
            atomic_json(root/'controller_prepared.json',dict(status='prepared_not_executed',
                        contract_sha256=sha256_file(cp),command=[sys.executable,'-u',artifact_path(Path(__file__)),
                        '--run-dir',artifact_path(root),'--run']))
            return
        if (root/'.done').exists():
            return verify_completed(root,contract)
        reject_previous_execution(root); check_bindings(contract)
        (root/'pid').write_text(str(os.getpid())+'\n')
        started = time.monotonic()
        try:
            while True:
                observation = main_observation(plan['main_run'])
                if observation['complete']:
                    break
                atomic_json(root/'state.json',dict(phase='running',case='await_audited_main_final',
                            main_step=observation['step'],actual_final_resume_completed=False))
                if time.monotonic()-started > contract['wait_max_seconds']:
                    raise RuntimeError('Final resume wait budget exhausted; inspect live main before any recovery')
                time.sleep(contract['poll_seconds'])
            check_bindings(contract)
            hw, budgets = admission(root,plan)
            atomic_json(root/'launch_hardware.json',hw); atomic_json(root/'launch_admission.json',budgets)
            cpus = sorted(set(plan['cpu_affinity']) & set(hw['cpu']['affinity']))
            if not cpus:
                raise RuntimeError('Diagnostic CPU affinity unavailable')
            atomic_json(root/'execution_started.json',dict(controller_pid=os.getpid(),unix=time.time(),plan_id=identity(plan)))
            # TMPDIR is the already reserved parent directory; probe scratch is
            # created only after complete audit and fresh source verification.
            env = dict(os.environ,OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',NUMEXPR_NUM_THREADS='2',RAYON_NUM_THREADS='1',
                       TMPDIR=str(Path(plan['scratch']).parent),PYTHONUNBUFFERED='1')
            command = ['taskset','-c',','.join(map(str,cpus)),sys.executable,'-u',str(Path(__file__).resolve()),
                       '--run-dir',str(root),'--worker']
            def guard(pid, elapsed, peak):
                state = json.loads((root/'worker_state.json').read_text()) if (root/'worker_state.json').exists() else {}
                atomic_json(root/'state.json',dict(phase='running',case=state.get('case','launching'),
                            worker_pid=pid,elapsed_seconds=elapsed,peak_pss_bytes=peak))
                if peak > contract['worker_pss_gib']*GIB or elapsed > contract['worker_max_seconds']:
                    raise RuntimeError('Final resume PSS/time budget exceeded')
                durable_bytes = size_tree(root); local_bytes = size_tree(Path(plan['scratch']))
                guard.storage_peak['durable'] = max(guard.storage_peak['durable'],durable_bytes)
                guard.storage_peak['local'] = max(guard.storage_peak['local'],local_bytes)
                if (durable_bytes > contract['durable_budget_gib']*GIB or local_bytes > contract['local_budget_gib']*GIB or
                        shutil.disk_usage(root).free < 200*GIB or shutil.disk_usage(Path(plan['scratch']).parent).free < 200*GIB):
                    raise RuntimeError('Final resume storage/reserve budget exceeded')
                if elapsed-guard.last_admission >= 30:
                    _, current = admission(root,plan)
                    atomic_json(root/'current_admission.json',current)
                    guard.last_admission = elapsed
            guard.last_admission = 0
            guard.storage_peak = dict(durable=0,local=0)
            metrics = supervise_command(command,root=root,env=env,guard=guard)
            metrics['sampled_peak_storage_bytes'] = guard.storage_peak
            if owned_processes(root):
                raise RuntimeError('Diagnostic ranks remain after coordinator exit')
            check_bindings(contract)
            report = json.loads((root/'worker_result.json').read_text())
            fresh = collect_result(root,json.loads((root/'spec.json').read_text()))
            if report != fresh:
                raise ValueError('Worker output differs from independently reread evidence')
            report['supervision'] = metrics
            atomic_json(root/'report.json',report)
            atomic_json(root/'.done',dict(report_sha256=sha256_file(root/'report.json'),contract_sha256=sha256_file(cp)))
            verify_completed(root,contract)
            atomic_json(root/'state.json',dict(phase='complete',case='actual_final8gpu_resume_verified',**metrics))
        except BaseException as exc:
            atomic_json(root/'state.json',dict(phase='error',error=str(exc),actual_final_resume_completed=False))
            raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare',action='store_true'); mode.add_argument('--run',action='store_true'); mode.add_argument('--worker',action='store_true')
    main(parser.parse_args())
