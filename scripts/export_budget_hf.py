"""Bounded final50k CPU HF export controller; inference serving is still separate.

Waits for actual main/native/resume/retention controllers, then freshly certifies
the source, converts and numerically checks HF, publishes and independently
rechecks content. Does not train, delete, upload or retry a failed attempt.
"""
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import shutil
import signal
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve,artifact_path,sha256_file,identity,atomic_json
from scripts.tiered_checkpoint_retention import exact_path,size_tree
from scripts.evaluate_final_rehearsal import check_bindings

GIB=2**30
SCRIPT='scripts/export_budget_hf.py'


def make_contract(main,root,native,resume,retention,*,runtime_control=None,storage_control=None):
    from inference.budget_final_source import make_contract as source_contract
    source=source_contract(main,root,native,resume,retention,runtime_control=runtime_control,
                           **({'storage_control':storage_control} if storage_control is not None else {}))
    names=[SCRIPT,'scripts/horizon_probe_process.py','scripts/horizon_process_handoff.py',
           'scripts/process_accounting.py','scripts/resource_admission.py','scripts/hardware_profile.py',
           'scripts/extended_horizon_storage.py','scripts/job_registry.py']
    return dict(version=1,purpose='budget_hf_export_controller',source=source,
        code={**source['code'],**{p:sha256_file(resolve(p)) for p in names}},inputs=source['inputs'],
        wait_max_seconds=22*86400,poll_seconds=30,worker_max_seconds=7200,
        worker_pss_gib=128,durable_budget_gib=100,additional_local_gib=0,
        cpu_affinity=list(range(32,64)),cpu_threads=16,
        env=dict(CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='16',MKL_NUM_THREADS='16',
                 OPENBLAS_NUM_THREADS='16',TOKENIZERS_PARALLELISM='false',RAYON_NUM_THREADS='1'),
        serving_verified=False,goal_complete=False)


def registration(root):
    from scripts.job_registry import validate
    root=exact_path(resolve(root))
    return validate(dict(run_id=root.name+'_hf_export',state_file=artifact_path(root/'state.json'),
        pid_file=artifact_path(root/'pid'),log_file=artifact_path(root/'supervision/console.log'),
        certificate=artifact_path(root/'.done'),report_file=artifact_path(root/'report.json'),
        pid_cmd_contains=SCRIPT,stall_seconds=1200,requires_review=True,
        proofs=[dict(path=artifact_path(root/'report.json'),field=['report_sha256']),
                dict(path=artifact_path(root/'contract.json'),field=['contract_sha256'])]))


def load_contract(root):
    root=exact_path(resolve(root));c=json.loads((root/'contract.json').read_text())
    prepared=json.loads((root/'prepared.json').read_text())
    if (prepared['contract_sha256']!=sha256_file(root/'contract.json') or
            c['purpose']!='budget_hf_export_controller' or
            resolve(c['source']['export_root'])!=root or not root.is_relative_to(resolve('artifacts')) or
            (c['worker_max_seconds'],c['worker_pss_gib'],c['durable_budget_gib'],c['additional_local_gib'],c['cpu_threads'])!=(7200,128,100,0,16) or
            c['cpu_affinity']!=list(range(32,64)) or c['source']['target_steps']!=50000):
        raise ValueError('Unreviewed prepared HF export contract')
    check_bindings(c)
    return c


def admission(root,c):
    from scripts.resource_admission import inspect
    from scripts.extended_horizon_storage import enforce_shared_space
    root=exact_path(resolve(root));s=c['source'];main=exact_path(resolve(s['main_run']))
    mc=json.loads((main/'contract.json').read_text())
    if 'final_storage' in s:
        if 'independent_runtime' in s:raise ValueError('Mixed final source/storage routes')
        from scripts.completed_budget_storage import view
        mc=view(main,s['final_storage'])
    if (resolve(s['export_root'])!=root or mc['shared_storage']['nfs_pools'].get('hf_and_serving')!=dict(paths=[str(root)],budget_bytes=100*GIB) or
            any(root.is_relative_to(resolve(s[k])) or resolve(s[k]).is_relative_to(root)
                for k in ('main_run','native_run','resume_run','retention_run'))):
        raise ValueError('Exact reserved independent export path differs')
    if size_tree(root)>100*GIB:
        raise RuntimeError('Export/staging/residue exceeds100GiB logical budget')
    hw=inspect();combined=mc['max_pss_gib']+256+256+16+128
    if (hw['gpus']!=mc['gpu_identity'] or
            hw['memory']['cache_aware_admission']['headroom_gib']<combined):
        raise RuntimeError('Combined main/final/export memory or device admission unavailable')
    capacity=enforce_shared_space(main,mc)
    return hw,dict(**capacity,combined_memory_gib=combined,export_additional_local_bytes=0)


def dependency_ready(root,script):
    root=exact_path(resolve(root))
    if (root/'.done').is_file():
        return True  # Marker only triggers fresh full source certification.
    state=json.loads((root/'state.json').read_text())
    if state.get('phase') in ('error','failed','paused'):
        raise RuntimeError('Final dependency requires inspection: '+str(root))
    pid=int((root/'pid').read_text())
    proc=Path('/proc')/str(pid)
    try:
        stamp=(proc/'stat').read_text().rsplit(')',1)[1].split()
        args=(proc/'cmdline').read_bytes().decode().split('\0')
        live=(stamp[0]!='Z' and '--worker' not in args and '--run-dir' in args and
              resolve(args[args.index('--run-dir')+1])==root and any(a.endswith(script) for a in args))
    except (FileNotFoundError,ProcessLookupError,ValueError):
        live=False
    if not live:
        if (root/'.done').is_file():return True
        raise RuntimeError('Actual dependency controller handle missing: '+str(root))
    return False


def ready(c):
    from scripts.evaluate_budget_native import main_observation
    s=c['source'];reference=s.get('independent_runtime')
    observation=main_observation(s['main_run'],reference) if reference is not None else main_observation(s['main_run'])
    dependencies={k:dependency_ready(s[k],script) for k,script in [
        ('native_run','evaluate_budget_native.py'),('resume_run','run_budget_final_resume.py'),
        ('retention_run','run_budget_retention_audit.py')]}
    return dict(main=observation,dependencies=dependencies,
                ready=observation['complete'] and all(dependencies.values()))


def owned_workers(root):
    root=resolve(root);found=[]
    for p in Path('/proc').glob('[0-9]*'):
        try:
            args=(p/'cmdline').read_bytes().decode().split('\0')
            if ((p/'stat').read_text().rsplit(')',1)[1].split()[0]!='Z' and
                    '--worker' in args and '--run-dir' in args and
                    resolve(args[args.index('--run-dir')+1])==root and any(a.endswith(SCRIPT) for a in args)):
                found.append(int(p.name))
        except (OSError,ValueError,IndexError):pass
    return found


def reject_previous(root):
    if owned_workers(root):raise RuntimeError('Existing live export worker; do not duplicate it')
    names=['execution_started.json','supervision','source','hf.copying','hf','worker_result.json']
    if any(os.path.lexists(root/n) for n in names):
        raise RuntimeError('Preserve previous export/partial publication; review before retry')


def worker(root):
    import torch
    from scripts.horizon_probe_process import OWNER_ENV
    from inference.budget_final_source import certify_source
    from inference.hf_export import stage_package
    from inference.verify_package import build_cases,verify_stage,publish_stage,verify_published
    from eval.run import load_checkpoint
    root=exact_path(resolve(root));c=load_contract(root);s=c['source']
    if s['framework']!=dict(torch=str(torch.__version__),cuda=torch.version.cuda) or os.environ.get('CUDA_VISIBLE_DEVICES')!='':
        raise ValueError('Export framework/CPU-only execution differs')
    handle=root/'supervision/handle.json';deadline=time.monotonic()+30
    while not handle.exists():
        if time.monotonic()>deadline:raise RuntimeError('Export supervision receipt timeout')
        time.sleep(.05)
    h=json.loads(handle.read_text());ticks=int(Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19])
    if h['pid']!=os.getpid() or h['process']['start_ticks']!=ticks or h['owner']!=os.environ.get(OWNER_ENV):
        raise ValueError('Actual export worker ownership differs')
    atomic_json(root/'worker_execution.json',dict(pid=os.getpid(),start_ticks=ticks,owner=h['owner'],
        contract_id=identity(c),framework=s['framework'],device='cpu'))
    torch.set_num_threads(c['cpu_threads']);torch.set_num_interop_threads(1)
    def notify(case):atomic_json(root/'worker_state.json',dict(case=case,pid=os.getpid(),unix=time.time()))
    last_admission=[-float('inf')]
    def resources():
        if size_tree(root)>100*GIB or shutil.disk_usage(root).free<200*GIB:
            raise RuntimeError('Export logical size or NFS safety reserve exceeded')
        if time.monotonic()-last_admission[0]>=30:
            _,capacity=admission(root,c);atomic_json(root/'worker_capacity.json',capacity)
            last_admission[0]=time.monotonic()
    notify('fresh_final_source_admission');live=certify_source(s,root/'source',guard=resources)
    notify('read_final_native_weights');native,meta=load_checkpoint(live.checkpoint,live.binding['tokenizer_sha256'],'cpu');live.check()
    if meta['step']!=50000:raise ValueError('Loaded final model step differs')
    notify('stage_fp32_hf');stage_package(native.state_dict(),native.c,resolve(s['tokenizer']),root/'hf.copying');live.check()
    cases=build_cases(resolve(s['tokenizer']),resolve(s['prompts']),context_length=4096)
    notify('compare_native_hf_fp32');validation=verify_stage(root/'hf.copying',native,resolve(s['tokenizer']),cases,guard=live.check)
    notify('publish_hf_manifest');publish_stage(root/'hf.copying',root/'hf',source_binding=live.binding,validation=validation,guard=live.check)
    verify_published(root/'hf',source_binding=live.binding,guard=live.check)
    atomic_json(root/'worker_result.json',dict(status='passed_final50k_hf_export_runtime_pending',
        source_binding=live.binding,publication_manifest_sha256=sha256_file(root/'hf/release_manifest.json'),
        numerical_sha256=sha256_file(root/'hf/numerical_validation.json'),
        source_attestation_sha256=sha256_file(root/'source/attestation.json'),
        worker_execution_sha256=sha256_file(root/'worker_execution.json'),
        vllm_verified=False,ollama_verified=False,goal_complete=False))
    notify('hf_verified_runtime_pending')


def verify_result(root,c):
    from train.final_resume_probe import file_stats,stat_record
    from inference.verify_package import verify_published
    check_bindings(c)
    result=json.loads((root/'worker_result.json').read_text());s=c['source']
    if (result['status']!='passed_final50k_hf_export_runtime_pending' or
            result['vllm_verified'] is not False or result['ollama_verified'] is not False or result['goal_complete'] is not False):
        raise ValueError('Unreviewed HF worker verdict')
    for key,path in [('source_attestation_sha256','source/attestation.json'),('worker_execution_sha256','worker_execution.json'),
                     ('publication_manifest_sha256','hf/release_manifest.json'),('numerical_sha256','hf/numerical_validation.json')]:
        if result[key]!=sha256_file(root/path):raise ValueError('Export result binding changed: '+path)
    att=json.loads((root/'source/attestation.json').read_text());binding=att['binding']
    cp=exact_path(resolve(binding['checkpoint']))
    if (result['source_binding']!=binding or binding['status']!='verified_actual_final50k_source' or
            binding.get('independent_runtime')!=s.get('independent_runtime') or
            binding.get('final_storage')!=s.get('final_storage') or
            cp!=resolve(s['main_run'])/'training/ckpt_0050000' or binding['step']!=50000 or
            binding['training_contract_id']!=s['parent_contract_id'] or binding['export_contract_id']!=identity(s) or
            binding['committed_tokens']!=104857600000 or
            any(binding[k] is not True for k in ('final_native_verified','actual8gpu_restore_verified','final_retention_verified'))):
        raise ValueError('Actual final50k source binding differs')
    from inference.budget_final_source import require_segment_counts
    audit=json.loads((root/'source/final_training_audit.json').read_text())
    require_segment_counts(audit,s)
    counts=audit['counts']
    if (identity(audit)!=binding['training_audit_id'] or audit['source_run']!=s['main_run'] or
            audit['validation_source'] or audit['through_step']!=50000 or audit['target_steps']!=50000 or
            counts['committed_step_rank_pairs']!=400000 or counts['committed_tokens']!=104857600000 or
            counts['successful_target_ranks']!=list(range(8)) or
            binding['committed_step_rank_pairs']!=counts['committed_step_rank_pairs'] or
            binding['source_tokens']!=counts['source_tokens'] or binding['segments']!=counts['segments'] or
            binding['replayed_tokens']!=counts['replayed_tokens_through_prefix']):
        raise ValueError('Final original segment/token publication evidence differs')
    content=binding['content']
    if ((content['model_tensors'],content['state_tensors'],content['rng_ranks'],content['payload_files'],content['full_payload_sha_refreshed'])!=(255,1020,8,8,True) or
            binding['tokenizer_sha256']!=sha256_file(resolve(s['tokenizer'])) or
            binding['quality_success_asserted'] is not False or binding['official_benchmark_measured'] is not False):
        raise ValueError('Source completeness/tokenizer/scope differs')
    def unchanged():
        if file_stats(cp)!=att['checkpoint_stats']:raise ValueError('Final source changed after worker certification')
        for p,stats in att['immutable_stats'].items():
            if stat_record(exact_path(resolve(p)))!=stats:raise ValueError('Final source evidence/input changed')
    unchanged();verify_published(root/'hf',source_binding=binding,guard=unchanged)
    numeric=json.loads((root/'hf/numerical_validation.json').read_text())
    from inference.verify_package import build_cases
    cases=build_cases(resolve(s['tokenizer']),resolve(s['prompts']),context_length=4096)
    if (numeric['case_inputs']!=cases or len(numeric['cases'])!=8 or numeric['atol']!=1e-5 or numeric['rtol']!=1e-4 or
            numeric['device']!='cpu' or numeric['dtype']!='float32' or
            numeric['cases'][-1]['positions']!=4096 or any(r['full_vocabulary']!=65536 for r in numeric['cases'])):
        raise ValueError('Actual3B fixed prompts/full-context numerical coverage differs')
    for expected,row in zip(cases,numeric['cases']):
        if (row['id']!=expected['id'] or row['category']!=expected['category'] or row['positions']!=len(expected['ids']) or
                not math.isfinite(row['max_absolute_error']) or row['max_absolute_error']<0):
            raise ValueError('Numerical case evidence differs')
        if len(expected['ids'])>1 and (row['scored_targets']!=len(expected['ids'])-1 or
                any(not math.isfinite(row[k]) or row[k]<0 for k in ('native_nll','hf_nll','cache_max_absolute_error'))):
            raise ValueError('Numerical NLL/cache evidence differs')
        if expected['greedy_tokens'] and (len(row['greedy_ids'])!=4 or any(type(i) is not int or not 0<=i<65536 for i in row['greedy_ids'])):
            raise ValueError('Greedy generation coverage differs')
    h=json.loads((root/'supervision/handle.json').read_text());run=json.loads((root/'supervision/result.json').read_text())
    execution=json.loads((root/'worker_execution.json').read_text())
    tail=[str(resolve(SCRIPT)),'--run-dir',str(root),'--worker']
    if (h['command'][-4:]!=tail or run['command']!=h['command'] or run['exit_code']!=0 or run['remaining_owned_processes'] or
            run['launcher_pid']!=h['pid'] or run['owner']!=h['owner'] or
            execution!=dict(pid=h['pid'],start_ticks=h['process']['start_ticks'],owner=h['owner'],contract_id=identity(c),framework=s['framework'],device='cpu')):
        raise ValueError('Actual supervised CPU export execution differs')
    unchanged();check_bindings(c)
    return result


def controller(root,c):
    if 'final_storage' in c['source']:
        from scripts.completed_budget_storage import admit_reference
        admit_reference(c['source']['main_run'],c['source']['final_storage'])
    if (root/'.done').exists():
        result=verify_result(root,c);report=json.loads((root/'report.json').read_text())
        marker=json.loads((root/'.done').read_text())
        if (marker!=dict(report_sha256=sha256_file(root/'report.json'),contract_sha256=sha256_file(root/'contract.json')) or
                any(report.get(k)!=v for k,v in result.items())):raise ValueError('HF completion evidence changed')
        return report
    reject_previous(root);(root/'pid').write_text(str(os.getpid())+'\n');began=time.monotonic()
    try:
        while True:
            observation=ready(c)
            if observation['ready']:break
            atomic_json(root/'state.json',dict(phase='running',case='await_main_and_final_diagnostics',**observation))
            if time.monotonic()-began>c['wait_max_seconds']:raise RuntimeError('Final HF wait budget exceeded')
            time.sleep(c['poll_seconds'])
        hw,capacity=admission(root,c)
        atomic_json(root/'execution_hardware.json',hw);atomic_json(root/'execution_capacity.json',capacity)
        cpus=sorted(set(c['cpu_affinity'])&set(hw['cpu']['affinity']))
        if len(cpus)<c['cpu_threads']:raise RuntimeError('Export CPU allocation unavailable')
        atomic_json(root/'execution_started.json',dict(controller_pid=os.getpid(),unix=time.time(),contract_id=identity(c)))
        command=['taskset','-c',','.join(map(str,cpus)),sys.executable,'-u',str(resolve(SCRIPT)),'--run-dir',str(root),'--worker']
        measured=dict(peak_pss_bytes=0,peak_logical_bytes=0);last=[-30]
        def guard(pid,elapsed,peak):
            from scripts.process_accounting import proportional_tree
            peak=max(peak,proportional_tree(os.getpid())['totals_bytes']['Pss'])
            measured['peak_pss_bytes']=max(measured['peak_pss_bytes'],peak)
            measured['peak_logical_bytes']=max(measured['peak_logical_bytes'],size_tree(root))
            if peak>128*GIB or elapsed>7200 or measured['peak_logical_bytes']>100*GIB or shutil.disk_usage(root).free<200*GIB:
                raise RuntimeError('HF export PSS/time/storage limit exceeded')
            if elapsed-last[0]>=30:
                _,now=admission(root,c);atomic_json(root/'current_capacity.json',now);last[0]=elapsed
            state=json.loads((root/'worker_state.json').read_text()) if (root/'worker_state.json').exists() else {}
            atomic_json(root/'state.json',dict(phase='running',case=state.get('case','launching'),worker_pid=pid,elapsed_seconds=elapsed,**measured))
        from scripts.run_budget_final_resume import supervise_command
        metrics=supervise_command(command,root=root,env=dict(os.environ,**c['env'],PYTHONUNBUFFERED='1'),guard=guard)
        if owned_workers(root):raise RuntimeError('Export worker remains alive after supervision')
        report=verify_result(root,c)
        report['supervision']=dict(**metrics,controller_measured=measured)
        atomic_json(root/'report.json',report)
        atomic_json(root/'.done',dict(report_sha256=sha256_file(root/'report.json'),contract_sha256=sha256_file(root/'contract.json')))
        atomic_json(root/'state.json',dict(phase='complete',case='hf_verified_runtime_pending',goal_complete=False,**measured))
        return report
    except BaseException as exc:
        atomic_json(root/'state.json',dict(phase='error',reason=type(exc).__name__+': '+str(exc),goal_complete=False))
        raise


def main(a):
    root=exact_path(resolve(a.run_dir))
    if a.worker:return worker(root)
    if a.prepare:
        if root.exists():raise ValueError('Prepare a new exact reserved export namespace')
        c=make_contract(a.main_run,root,a.native_run,a.resume_run,a.retention_run,runtime_control=getattr(a,'runtime_control',None),
                        storage_control=getattr(a,'storage_control',None))
        if 'final_storage' in c['source']:
            from scripts.completed_budget_storage import admit_reference
            admit_reference(c['source']['main_run'],c['source']['final_storage'])
        hw,capacity=admission(root,c);root.mkdir(parents=True,exist_ok=False)
        atomic_json(root/'contract.json',c);atomic_json(root/'preparation_hardware.json',hw);atomic_json(root/'preparation_capacity.json',capacity)
        atomic_json(root/'job_spec.json',registration(root))
        atomic_json(root/'prepared.json',dict(status='prepared_not_executed',contract_sha256=sha256_file(root/'contract.json'),actual_hf_export_verified=False))
        return
    c=load_contract(root)
    with (root/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        def interrupted(_s,_f):raise InterruptedError('Export controller interrupted; preserve evidence')
        previous=signal.signal(signal.SIGTERM,interrupted)
        try:return controller(root,c)
        finally:signal.signal(signal.SIGTERM,previous)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',default='artifacts/3b_base_50k_v1')
    p.add_argument('--main-run',default='runs/3b_full_50k_v1');p.add_argument('--native-run',default='runs/3b_50k_final_native_v2')
    p.add_argument('--resume-run',default='runs/3b_50k_final_resume_v2');p.add_argument('--retention-run',default='runs/3b_50k_final_retention_v2')
    p.add_argument('--runtime-control')
    p.add_argument('--storage-control')
    m=p.add_mutually_exclusive_group(required=True)
    m.add_argument('--prepare',action='store_true');m.add_argument('--run',action='store_true');m.add_argument('--worker',action='store_true')
    main(p.parse_args())
