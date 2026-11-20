"""Bounded CPU-only final retention audit after actual main completion.

The approved main and both storage namespaces are read-only. A completion
marker triggers the worker; the worker must audit actual8rank35000 and freshly
hash every retained checkpoint. Interrupted evidence is preserved, never
automatically retried or repaired. This is not whole-Goal completion.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve, artifact_path, atomic_json, sha256_file
from scripts.evaluate_final_horizon import main_observation
from scripts.evaluate_final_rehearsal import check_bindings
from scripts.audit_checkpoint_retention import existing_ancestor
from scripts.run_final_horizon_resume import supervise_command
from scripts.tiered_checkpoint_retention import size_tree
from train.final_resume_probe import file_stats

GIB = 2**30


def make_contract(main_run, native_run, resume_run):
    main, native, resume = map(resolve, [main_run, native_run, resume_run])
    mc = json.loads((main/'contract.json').read_text())
    from scripts.supervise_horizon_training import require_authorization
    require_authorization(mc)
    cfg = json.loads((main/'main.json').read_text())
    nc = json.loads((native/'contract.json').read_text())
    rc = json.loads((resume/'contract.json').read_text())
    plan = json.loads((resume/'plan.json').read_text())
    check_bindings(nc); check_bindings(rc)
    if (cfg['model'] != '3b' or cfg['max_steps'] != 35000 or cfg['global_tokens'] != 2097152 or
            mc['configurations']['main.json'] != sha256_file(main/'main.json') or
            nc['purpose'] != 'final_native_main_evaluation' or nc.get('final_step') != 35000 or plan.get('parent_step') != 35000 or rc['purpose'] != 'final_horizon_resume_controller' or
            resolve(nc['main_run']) != main or resolve(plan['main_run']) != main or
            resolve(plan['native_run']) != native or resolve(rc['main_run']) != main):
        raise ValueError('Final retention dependencies/target differ')
    names = ['scripts/run_final_horizon_retention_audit.py', 'scripts/audit_horizon_retention.py',
             'scripts/audit_horizon_training.py', 'scripts/evaluate_final_horizon.py',
             'scripts/run_final_horizon_resume.py', 'scripts/verify_final_resume_probe.py',
             'train/final_horizon_resume_probe.py', 'scripts/validate_tiered_retention_copy.py',
             'scripts/audit_checkpoint_retention.py', 'scripts/horizon_storage_plan.py',
             'scripts/horizon_probe_process.py']
    inputs = [main/'contract.json', main/'main.json', main/'authorization.json',
              main/'training/training_contract.json', native/'contract.json',
              resume/'contract.json', resume/'plan.json', resume/'prepared.json']
    return dict(version=1, purpose='final_horizon_retention_audit_controller', main_run=artifact_path(main),
                native_run=artifact_path(native), resume_run=artifact_path(resume),
                code={**mc['code'], **{p:sha256_file(resolve(p)) for p in names}},
                inputs={**mc['evidence'], **{artifact_path(p):sha256_file(p) for p in inputs}},
                poll_seconds=30, wait_max_seconds=22*86400, worker_max_seconds=1200,
                max_pss_gib=16, durable_budget_gib=1, local_additional_budget_gib=0,
                cpu_affinity=list(range(32)), hash_workers=8,
                env=dict(CUDA_VISIBLE_DEVICES='', OMP_NUM_THREADS='2', NUMEXPR_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2', RAYON_NUM_THREADS='1'),
                source_contract_sha256=sha256_file(main/'contract.json'),
                retention_nfs_keep=mc['tiered_retention']['nfs_keep'],
                adaptive_local_pruning=mc['tiered_retention'].get('adaptive_local_pruning',False),
                scope='Fresh full SHA of actual policy-bounded NFS and local history; NFS-only resume and optional/adaptive local history. No main mutation, repair, pruning, GPU, upload or Goal completion.')


def validate_output(root, contract):
    root = resolve(root)
    if root == resolve('runs') or not root.is_relative_to(resolve('runs')):
        raise ValueError('Use an independent run under runs')
    mc = json.loads((resolve(contract['main_run'])/'contract.json').read_text())
    protected = [resolve(contract[k]) for k in ['main_run','native_run','resume_run']]
    protected += [Path(mc['tiered_retention'][k]) for k in ['local_root','temp_root']]
    protected += [resolve(mc['legacy_retention']['source_run'])]
    protected += [Path(mc['legacy_retention']['source_policy'][k]) for k in ['local_root','temp_root']]
    if mc['space_reservations']['nfs'].get(str(root)) != GIB:
        raise ValueError('Exact combined final retention reservation missing')
    if any(root.is_relative_to(p) or p.is_relative_to(root) for p in protected):
        raise ValueError('Audit output overlaps a protected source')
    if any(p.is_symlink() for p in [root,*root.parents]):
        raise ValueError('Audit output symlink rejected')


def admission(root, contract):
    from scripts.resource_admission import inspect
    from scripts.horizon_storage_plan import enforce_runtime_space
    hw=inspect();main=resolve(contract['main_run']);mc=json.loads((main/'contract.json').read_text())
    nc=json.loads((resolve(contract['native_run'])/'contract.json').read_text())
    rc=json.loads((resolve(contract['resume_run'])/'contract.json').read_text())
    validate_output(root,contract)
    if (hw['gpus']!=mc['gpu_identity'] or nc['durable_budget_gib']!=8 or rc['durable_budget_gib']!=1 or
            nc['host_rss_gib']!=256 or rc['worker_pss_gib']!=256 or contract['max_pss_gib']!=16):
        raise RuntimeError('Reviewed final dependency/resource identity differs')
    combined=mc['max_pss_gib']+256+256+16
    if hw['memory']['cache_aware_admission']['headroom_gib']<combined:
        raise RuntimeError('Combined final audit memory admission unavailable')
    capacity=enforce_runtime_space(main,mc)
    if size_tree(root)>GIB:raise RuntimeError('Final audit metadata exceeds reserved1GiB')
    return hw,dict(**capacity,combined_memory_gib=combined,audit_additional_local_bytes=0)


def owned_workers(root):
    found = []
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            args = (proc/'cmdline').read_bytes().decode(errors='replace').split('\0')
            if ((proc/'stat').read_text().split(') ',1)[1].split()[0] != 'Z' and
                    any(a.endswith('run_final_horizon_retention_audit.py') for a in args) and '--worker' in args and
                    '--run-dir' in args and resolve(args[args.index('--run-dir')+1]) == resolve(root)):
                found.append(int(proc.name))
        except (OSError, ValueError, IndexError):
            pass
    return sorted(found)


def reject_previous(root):
    live = owned_workers(root)
    if live:
        raise RuntimeError('Actual orphan audit handles exist: '+str(live))
    if any((root/p).exists() for p in ['execution_started.json','supervision','worker_handle.json','worker_result.json','worker_console.log']):
        raise RuntimeError('Preserve prior audit attempt and review before a new namespace')


def require_result(result, contract):
    wanted=[f'ckpt_{s:07d}' for s in [34000,34500,35000]]
    if (result['status']!='passed_final_horizon_retention_content' or result['production'] is not True or
            result['final'] is not True or result['full_payload_sha_refreshed'] is not True or
            result['source_run']!=contract['main_run'] or result['through_step']!=35000 or
            result['source_contract_sha256']!=contract['source_contract_sha256'] or
            result['audit_code_sha256']!=contract['code']['scripts/audit_horizon_retention.py'] or
            result['issues'] or result['residues'] or result['overlap']):
        raise ValueError('Actual final horizon retention verdict differs')
    if (sorted(result['nfs'])!=wanted or result['nfs_resume_candidates']!=wanted or result['expected_nfs']!=wanted or
            len(result['local'])>15 or result['distinct_checkpoints']!=len(result['nfs'])+len(result['local']) or
            result['certificate_count']!=70 or len(result['branch_inventory'])!=2 or
            result['protected_parent_counted_as_normal_history'] is not False):
        raise ValueError('Actual global3+15/70 receipt coverage differs')
    all_names=set()
    for branch in result['branch_inventory']:
        names=set(branch['certificates'])
        if all_names&names:raise ValueError('Duplicate certificate across contracts')
        all_names.update(names)
        if branch['local_namespace_missing'] and branch['local']:raise ValueError('Lost local namespace counted as retained')
    combined_nfs={n:v for b in result['branch_inventory'] for n,v in b['nfs'].items()}
    combined_local={n:v for b in result['branch_inventory'] for n,v in b['local'].items()}
    if (result['nfs']!=combined_nfs or result['local']!=combined_local or
            set(combined_nfs)&set(combined_local) or sorted(combined_local)!=result['expected_local']):
        raise ValueError('Global/branch resident inventory differs')
    if all_names!={f'ckpt_{s:07d}' for s in range(500,35001,500)}:
        raise ValueError('Absolute final certificate schedule differs')
    for cp in [*result['nfs'].values(),*result['local'].values(),result['protected_parent']]:
        if (cp['model_tensors']!=255 or cp['state_tensors']!=1020 or cp['rng_ranks']!=8 or
                cp['payload_files']!=8 or cp['full_payload_sha_refreshed'] is not True):
            raise ValueError('Full3B model/optimizer/RNG fresh content coverage missing')
    audit=result['training_audit']
    if (audit['validation_source'] or audit['through_step']!=35000 or
            audit['counts']['committed_step_rank_pairs']!=280000 or
            audit['counts']['child_contract_id']!=result['branch_inventory'][1]['training_contract_id'] or
            audit['counts']['committed_tokens']!=73400320000 or
            audit['counts']['successful_target_ranks']!=list(range(8))):
        raise ValueError('Actual35k target audit missing')


def verify_current(result, contract):
    check_bindings(contract);require_result(result,contract)
    main=resolve(contract['main_run']);mc=json.loads((main/'contract.json').read_text())
    branches=result['branch_inventory'];source=resolve(mc['legacy_retention']['source_run'])
    if [resolve(b['root']) for b in branches]!=[source,main]:raise ValueError('Audited owner roots differ')
    policies=[mc['legacy_retention']['source_policy'],mc['tiered_retention']]
    paths=set()
    for row,policy in zip(branches,policies):
        if row['local_root']!=policy['local_root']:raise ValueError('Audited local owner differs')
        paths.update(str(resolve(row['root'])/'training'/n) for n in row['nfs'])
        paths.update(str(Path(row['local_root'])/'checkpoints'/n) for n in row['local'])
    receipt=json.loads((main/'parent_handoff.json').read_text())
    protected=resolve(receipt['parent_checkpoint'])
    if result['protected_parent']['path']!=str(protected):raise ValueError('Protected parent scope differs')
    paths.add(str(protected))
    if set(result['resident_file_stats'])!=paths:raise ValueError('Resident full-state proof coverage differs')
    for name,stats in result['resident_file_stats'].items():
        p=Path(name)
        if any(x.is_symlink() for x in [p,*p.parents]) or file_stats(p)!=stats:
            raise ValueError('Resident checkpoint changed after fresh SHA')
    bindings=dict(result['metadata_evidence'])
    for name,digest in result['training_audit']['evidence'].items():
        if name in bindings and bindings[name]!=digest:raise ValueError('Conflicting content proof')
        bindings[name]=digest
    for name,digest in bindings.items():
        if sha256_file(resolve(name))!=digest:raise ValueError('Audited metadata/ledger changed')
    for name,expected in result['observed_directories'].items():
        p=Path(name)
        if any(x.is_symlink() for x in [p,*p.parents]):raise ValueError('Audited namespace became symlink')
        observed=[[x.name,x.is_dir(),x.is_symlink()] for x in sorted(p.iterdir())] if p.exists() else None
        normalized=[list(r) for r in expected] if expected is not None else None
        if observed!=normalized:raise ValueError('Audited namespace changed after worker')


def worker(root):
    contract = json.loads((root/'contract.json').read_text()); check_bindings(contract)
    validate_output(root,contract)
    if not (root/'execution_started.json').exists() or os.environ.get('CUDA_VISIBLE_DEVICES') != '':
        raise ValueError('Supervised CPU-only audit receipt/environment missing')
    from scripts.horizon_probe_process import OWNER_ENV
    owner=os.environ.get(OWNER_ENV);handle_path=root/'supervision/handle.json';deadline=time.monotonic()+30
    if not owner:raise ValueError('Owned audit environment missing')
    while not handle_path.exists():
        if time.monotonic()>=deadline:raise RuntimeError('Owned audit handle publication timeout')
        time.sleep(.05)
    handle=json.loads(handle_path.read_text())
    ticks=int(Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')',1)[1].split()[19])
    if handle['pid']!=os.getpid() or handle['owner']!=owner or handle['process']['start_ticks']!=ticks:
        raise ValueError('Owned audit process identity differs')
    from scripts.audit_horizon_retention import audit_run
    result = audit_run(contract['main_run'],final=True)
    verify_current(result,contract)
    atomic_json(root/'worker_result.json',result)


def verify_completed(root, contract):
    marker = json.loads((root/'.done').read_text())
    if marker != dict(report_sha256=sha256_file(root/'report.json'),contract_sha256=sha256_file(root/'contract.json')):
        raise ValueError('Final audit completion binding differs')
    report = json.loads((root/'report.json').read_text()); verify_current(report,contract)
    return report


def controller(root, contract):
    if (root/'.done').exists():
        return verify_completed(root,contract)
    reject_previous(root); check_bindings(contract)
    (root/'pid').write_text(str(os.getpid())+'\n'); started = time.monotonic()
    try:
        while True:
            observation = main_observation(contract['main_run'])
            if observation['complete']:
                break
            atomic_json(root/'state.json',dict(phase='running',case='await_actual_main_final',main_step=observation['step']))
            if time.monotonic()-started > contract['wait_max_seconds']:
                raise RuntimeError('Final retention wait time budget')
            rss = int(next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('VmRSS:')))*1024
            if size_tree(root)>GIB or rss>512*2**20:
                raise RuntimeError('Final retention waiting metadata/memory budget')
            time.sleep(contract['poll_seconds'])
        hw, capacity = admission(root,contract)
        atomic_json(root/'execution_hardware.json',hw); atomic_json(root/'execution_capacity.json',capacity)
        cpus = sorted(set(contract['cpu_affinity']) & set(hw['cpu']['affinity']))
        if not cpus:
            raise RuntimeError('Final retention CPU affinity unavailable')
        atomic_json(root/'execution_started.json',dict(controller_pid=os.getpid(),unix=time.time()))
        env = dict(os.environ,**contract['env'],PYTHONUNBUFFERED='1')
        command = ['taskset','-c',','.join(map(str,cpus)),sys.executable,'-u',str(Path(__file__).resolve()),'--run-dir',str(root),'--worker']
        began = time.monotonic(); measured = dict(peak_pss_bytes=0,peak_durable_bytes=0)
        policy = json.loads((resolve(contract['main_run'])/'contract.json').read_text())['tiered_retention']
        def guard(pid, elapsed, peak):
            from scripts.process_accounting import proportional_tree
            measured['peak_pss_bytes'] = max(measured['peak_pss_bytes'],proportional_tree(os.getpid())['totals_bytes']['Pss'],peak)
            measured['peak_durable_bytes'] = max(measured['peak_durable_bytes'],size_tree(root))
            if measured['peak_pss_bytes']>contract['max_pss_gib']*GIB or time.monotonic()-began>contract['worker_max_seconds']:
                raise RuntimeError('Final retention CPU audit memory/time budget')
            if (measured['peak_durable_bytes']>GIB or shutil.disk_usage(root).free<200*GIB or
                    shutil.disk_usage(existing_ancestor(policy['local_root'])).free<200*GIB):
                raise RuntimeError('Final retention proof storage/reserve budget')
            if elapsed-guard.last_admission>=30:
                _, current = admission(root,contract); atomic_json(root/'current_capacity.json',current)
                guard.last_admission = elapsed
            atomic_json(root/'state.json',dict(phase='running',case='fresh_full_retention_audit',worker_pid=pid,elapsed_seconds=time.monotonic()-began,**measured))
        guard.last_admission = -30
        metrics = supervise_command(command,root=root,env=env,guard=guard)
        if owned_workers(root):
            raise RuntimeError('Audit worker remains alive after supervision')
        result = json.loads((root/'worker_result.json').read_text()); verify_current(result,contract)
        guard(metrics['launcher_pid'],time.monotonic()-began,metrics['peak_pss_bytes'])
        result['supervision'] = dict(**metrics,controller_and_worker_peak=measured,wall_seconds=time.monotonic()-began)
        result['goal_complete'] = False
        atomic_json(root/'report.json',result)
        atomic_json(root/'.done',dict(report_sha256=sha256_file(root/'report.json'),contract_sha256=sha256_file(root/'contract.json')))
        atomic_json(root/'state.json',dict(phase='complete',case='actual_final_retention_verified',goal_complete=False,**measured))
    except BaseException as exc:
        atomic_json(root/'state.json',dict(phase='error',reason=type(exc).__name__+': '+str(exc),goal_complete=False))
        raise


def main(args):
    root = resolve(args.run_dir)
    if args.worker:
        return worker(root)
    if args.prepare:
        if root.exists():
            raise ValueError('Prepare a new independent audit namespace')
        c = make_contract(args.main_run,args.native_run,args.resume_run); validate_output(root,c)
        root.mkdir(parents=True)
        atomic_json(root/'contract.json',c)
        hw, capacity = admission(root,c)
        atomic_json(root/'preparation_hardware.json',hw); atomic_json(root/'preparation_capacity.json',capacity)
        atomic_json(root/'prepared.json',dict(status='prepared_not_executed',contract_sha256=sha256_file(root/'contract.json'),actual_final_retention_verified=False))
        return
    c = json.loads((root/'contract.json').read_text()); validate_output(root,c)
    prepared = json.loads((root/'prepared.json').read_text())
    if prepared['contract_sha256'] != sha256_file(root/'contract.json'):
        raise ValueError('Prepared audit contract changed')
    with (root/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        def interrupted(_signum,_frame):
            raise InterruptedError('Audit controller interrupted; preserve outputs')
        previous = signal.signal(signal.SIGTERM,interrupted)
        try:
            return controller(root,c)
        finally:
            signal.signal(signal.SIGTERM,previous)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',required=True)
    p.add_argument('--main-run',default='runs/3b_full_35k_v1')
    p.add_argument('--native-run',default='runs/3b_35k_final_native_v1')
    p.add_argument('--resume-run',default='runs/3b_35k_final_resume_v1')
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare',action='store_true');mode.add_argument('--run',action='store_true');mode.add_argument('--worker',action='store_true')
    main(p.parse_args())
