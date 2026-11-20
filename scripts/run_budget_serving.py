"""Supervise final50k loopback API checks after verified offline inference.

No upload, training mutation, automatic failed retry or persistent public API.
The server exits after the bounded tests; user-serving commands are separate.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve, artifact_path, sha256_file, identity, atomic_json
from inference.runtime_package import exact_path, authenticate_release
from inference.serving_process import listener_ready, verify_listener, process_ticks

SCRIPT = 'scripts/run_budget_serving.py'
WORKER = 'inference/budget_vllm_server.py'
GIB = 2**30


def make_contract(runtime):
    from scripts.run_budget_vllm import load_contract
    runtime = exact_path(resolve(runtime)); base = load_contract(runtime)
    c = dict(base)
    names = [SCRIPT, WORKER, 'inference/serving_process.py', 'inference/serving_validation.py']
    c.update(purpose='final50k_vllm_serving', root=artifact_path(runtime.parent/'vllm_serving_v1'),
        runtime_root=artifact_path(runtime), model_alias='llm-dense-3b-50k', startup_seconds=900,
        code={**base['code'], **{n:sha256_file(resolve(n)) for n in names}},
        inputs={**base['inputs'], **{artifact_path(runtime/n):sha256_file(runtime/n) for n in ['contract.json','prepared.json']}},
        dependency_scope='Authenticate completed offline runtime and final HF before owning GPU/API',
        local_subdirectory='serving_50k_v2')
    return c


def load_contract(root):
    from scripts.evaluate_final_rehearsal import check_bindings
    root = exact_path(resolve(root)); c = json.loads((root/'contract.json').read_text())
    if (json.loads((root/'prepared.json').read_text())['contract_sha256'] != sha256_file(root/'contract.json')
            or c['purpose'] != 'final50k_vllm_serving' or resolve(c['root']) != root
            or root != exact_path(resolve(c['export_root']))/'vllm_serving_v1'
            or resolve(c['runtime_root']) != root.parent/'vllm_runtime_v1'
            or c['model_alias'] != 'llm-dense-3b-50k' or c['local_subdirectory'] != 'serving_50k_v2'
            or (c['max_seconds'], c['max_pss_gib'], c['startup_seconds'], c['durable_gib']) != (1800,64,900,1)):
        raise ValueError('Unreviewed serving contract')
    check_bindings(c)
    return c


def dependency_ready(c):
    root = resolve(c['runtime_root'])
    if (root/'.done').is_file():
        return True  # Fresh independent runtime/source review follows.
    state = json.loads((root/'state.json').read_text())
    if state.get('phase') != 'running':
        raise RuntimeError('Offline runtime dependency requires review')
    pid = int((root/'pid').read_text()); proc = Path(f'/proc/{pid}')
    try:
        process_ticks(pid)
        args = (proc/'cmdline').read_bytes().decode().rstrip('\0').split('\0')
        run = resolve(args[args.index('--export-run')+1]) if '--export-run' in args else resolve('artifacts/3b_base_50k_v1')
        if ('--run' not in args or not any(a.endswith('scripts/run_budget_vllm.py') for a in args)
                or run/'vllm_runtime_v1' != root):
            raise ValueError('Runtime controller command differs')
    except (FileNotFoundError, ProcessLookupError, ValueError):
        if (root/'.done').is_file():
            return True
        raise RuntimeError('Actual runtime dependency controller missing')
    return False


def authenticate_sources(c):
    from scripts.run_budget_vllm import load_contract, controller
    root = resolve(c['runtime_root'])
    if not (root/'.done').is_file():
        raise ValueError('Offline runtime is not complete')
    report = controller(root, load_contract(root))
    if report['status'] != 'passed_actual_final50k_vllm_inference' or report['source_binding']['step'] != 50000:
        raise ValueError('Wrong actual final runtime source')
    if report['source_binding'].get('independent_runtime')!=c.get('independent_runtime'):
        raise ValueError('Offline/API independent source runtime differs')
    if report['source_binding'].get('final_storage')!=c.get('final_storage'):
        raise ValueError('Offline/API separate storage binding differs')
    return dict(contract_id=identity(c), source_binding=report['source_binding'],
        manifest_sha256=report['manifest_sha256'], runtime_report_sha256=sha256_file(root/'report.json'),
        runtime_marker_sha256=sha256_file(root/'.done'))


def admission(root, c):
    from scripts.run_budget_vllm import load_contract, admission as runtime_admission
    from scripts.tiered_checkpoint_retention import size_tree
    base = load_contract(resolve(c['runtime_root']))
    if c.get('final_storage')!=base.get('final_storage'):
        raise ValueError('Runtime/API separate storage binding differs')
    hw, capacity = runtime_admission(root, base)
    # This checks the whole export root, not merely this API directory. Both
    # runtime proofs share the existing100GiB budget; they do not add100GiB.
    if size_tree(resolve(c['export_root'])) > 100*GIB:
        raise RuntimeError('Combined export/runtime storage cap exceeded')
    return hw, dict(**capacity, serving_part_of_export_budget=True, gpu_runtime_sequential=True)


def command(root, c):
    return ['taskset','-c',','.join(map(str,c['cpu_affinity'])),str(Path(c['venv'])/'bin/python'),
            '-u',str(resolve(WORKER)),'--run-dir',str(root)]


def reject_previous(root):
    root = resolve(root)
    for name in ['server','execution_started.json','admitted.json','server_execution.json','http_checks.json','report.json','.done']:
        if (root/name).exists():
            raise RuntimeError('Preserve prior serving attempt: '+name)
    for p in Path('/proc').glob('[0-9]*'):
        try:
            args = (p/'cmdline').read_bytes().decode().split('\0')
            if (any(a.endswith(WORKER) for a in args) and '--run-dir' in args
                    and resolve(args[args.index('--run-dir')+1]) == root):
                raise RuntimeError('Existing serving worker; do not duplicate')
        except (OSError, ValueError, IndexError):
            pass


def gpu_sample(owned):
    def query(args):
        return subprocess.check_output(['nvidia-smi',*args,'--format=csv,noheader,nounits'],text=True,timeout=5)
    rows = [line.split(', ') for line in query(['--query-gpu=index,uuid,name,memory.used,memory.total,utilization.gpu']).splitlines()]
    gpu = next(r for r in rows if r[0] == '0')
    if 'H200' not in gpu[2]:
        raise ValueError('Wrong serving GPU')
    apps = []
    live = owned.scan()
    for row in query(['--query-compute-apps=pid,gpu_uuid,used_memory']).splitlines():
        pid, uuid, memory = row.split(', ')
        if uuid == gpu[1]:
            if int(pid) not in live:
                raise RuntimeError('Unowned process is using the serving GPU')
            apps.append(dict(pid=int(pid), memory_mib=float(memory)))
    return dict(unix=time.time(), gpu_index=0, uuid=gpu[1], name=gpu[2],
        memory_used_mib=float(gpu[3]), memory_total_mib=float(gpu[4]), utilization_percent=float(gpu[5]),
        owned_compute_processes=apps)


def run_owned_http(root, c, env, live_package, resource_guard, *, sample=gpu_sample):
    """Monitor resource limits on main thread while bounded HTTP runs elsewhere."""
    from scripts.horizon_probe_process import OwnedCommand
    from inference.serving_validation import LocalClient, run_checks
    from tokenizers import Tokenizer
    root = resolve(root); directory = root/'server'
    raw = Tokenizer.from_file(str(resolve(c['tokenizer'])))
    prompts = [json.loads(l) for l in resolve(c['prompts']).read_text().splitlines() if l.strip()]
    directory.mkdir()
    owned = OwnedCommand(command(root,c), directory, env)
    handle = json.loads((directory/'handle.json').read_text())
    stop = threading.Event(); finished = threading.Event(); state = {}; thread = None
    samples = []; last_sample = -5.; shutting_down = None; record = None

    def client_guard():
        if stop.is_set():
            raise RuntimeError('Serving checks cancelled')
        live_package.check()
        verify_listener(record, handle, command(root,c))

    def requests():
        try:
            client = LocalClient(record['port'], guard=client_guard)
            state['checks'] = run_checks(client,raw,prompts,model=c['model_alias'])
        except BaseException as exc:
            state['error'] = exc
        finally:
            finished.set()

    def guard(job, elapsed, peak):
        nonlocal thread, record, last_sample, shutting_down
        resource_guard(job.proc.pid, elapsed, peak)
        if elapsed > c['max_seconds'] or peak > c['max_pss_gib']*GIB:
            raise RuntimeError('Serving runtime budget exceeded')
        live_package.check()
        if elapsed-last_sample >= 5 or (finished.is_set() and shutting_down is None):
            samples.append(sample(job)); last_sample=elapsed
            atomic_json(root/'gpu_samples.json', dict(samples=samples, peak_observed_mib=max(s['memory_used_mib'] for s in samples)))
        if thread is None:
            if elapsed > c['startup_seconds']:
                raise RuntimeError('Serving startup deadline exceeded')
            path = root/'server_execution.json'
            if path.exists():
                candidate = json.loads(path.read_text())
                if listener_ready(candidate,handle,command(root,c)):
                    record = candidate
                    atomic_json(root/'listener_verified.json', dict(**verify_listener(record,handle,command(root,c)),
                        server_execution_sha256=sha256_file(path), owner=handle['owner']))
                    thread = threading.Thread(target=requests,daemon=True); thread.start()
        if finished.is_set():
            if 'error' in state:
                raise state['error']
            if shutting_down is None:
                client_guard()
                atomic_json(root/'http_checks.json',state['checks'])
                atomic_json(root/'http_complete.json',dict(server_execution_sha256=sha256_file(root/'server_execution.json'),
                    checks_sha256=sha256_file(root/'http_checks.json'),seconds=elapsed,owner=handle['owner']))
                job.send([job.proc.pid],signal.SIGTERM); shutting_down=elapsed
            elif elapsed-shutting_down > 30:
                raise RuntimeError('Server did not exit after completed API checks')
        if job.proc.poll() is not None and not finished.is_set():
            raise RuntimeError('Server exited before API checks completed')

    try:
        metrics = owned.run(guard,interval=.25)
        if 'checks' not in state:
            raise RuntimeError('Missing actual HTTP results')
        return metrics
    finally:
        stop.set()
        if thread is not None:
            thread.join(timeout=1)


def verify_result(root, c):
    from scripts.evaluate_final_rehearsal import check_bindings
    from inference.serving_validation import run_checks
    from inference.budget_vllm_server import server_arguments
    from tokenizers import Tokenizer
    import math
    check_bindings(c); admitted = authenticate_sources(c)
    if json.loads((root/'admitted.json').read_text()) != admitted:
        raise ValueError('Serving admission/source changed')
    handle = json.loads((root/'server/handle.json').read_text())
    result = json.loads((root/'server/result.json').read_text())
    execution = json.loads((root/'server_execution.json').read_text())
    exited = json.loads((root/'server_exit.json').read_text())
    listener = json.loads((root/'listener_verified.json').read_text())
    http_done = json.loads((root/'http_complete.json').read_text())
    checks = json.loads((root/'http_checks.json').read_text())
    samples = json.loads((root/'gpu_samples.json').read_text())
    resources = json.loads((root/'resource_peak.json').read_text())
    if (result['exit_code'] != 0 or result['remaining_owned_processes'] or result['command'] != command(root,c)
            or result['owner'] != handle['owner'] or result['launcher_pid'] != handle['pid']
            or handle['command'] != command(root,c) or execution['pid'] != handle['pid']
            or execution['start_ticks'] != handle['process']['start_ticks'] or execution['owner'] != handle['owner']
            or execution['contract_id'] != identity(c) or execution['admission_sha256'] != sha256_file(root/'admitted.json')
            or execution['manifest_sha256'] != admitted['manifest_sha256'] or execution['versions'] != c['versions']
            or execution['visible_device_count'] != 1 or execution['cuda_build'] != '12.8' or 'H200' not in execution['device_name']
            or execution['argv'] != server_arguments(c) or execution['host'] != '127.0.0.1'
            or any(listener[k] != execution[k] for k in ['pid','start_ticks','inode','host','port','owner'])
            or listener['listening'] is not True or listener['server_execution_sha256'] != sha256_file(root/'server_execution.json')
            or http_done['server_execution_sha256'] != sha256_file(root/'server_execution.json')
            or http_done['checks_sha256'] != sha256_file(root/'http_checks.json') or http_done['owner'] != handle['owner']
            or exited['status'] != 'server_context_exited' or exited['contract_id'] != identity(c)
            or exited['execution_sha256'] != sha256_file(root/'server_execution.json')):
        raise ValueError('Actual API server/process/source provenance differs')
    for seconds in [result['elapsed_seconds'], exited['seconds'], http_done['seconds']]:
        if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0 < seconds <= c['max_seconds']:
            raise ValueError('Serving elapsed budget evidence differs')
    if not 0 < result['peak_pss_bytes'] <= resources['combined_peak_pss_bytes'] <= c['max_pss_gib']*GIB:
        raise ValueError('Serving memory budget evidence differs')
    # Re-run the same pure validation over recorded exact request/response pairs;
    # production HTTP identity above is independently required.
    raw = Tokenizer.from_file(str(resolve(c['tokenizer'])))
    prompts = [json.loads(l) for l in resolve(c['prompts']).read_text().splitlines() if l.strip()]
    class Replay:
        def __init__(self): self.index=0
        def request(self,path,payload=None,*,stream=False):
            if path=='/health': return checks['health']
            if path=='/v1/models': return checks['models']
            row=checks['requests'][self.index]; self.index+=1
            if row['request']!=payload or bool(payload.get('stream'))!=stream:
                raise ValueError('Actual HTTP request suite changed')
            return row['reply']
    replay = Replay(); reviewed = run_checks(replay,raw,prompts,model=c['model_alias'])
    if reviewed != checks or replay.index != 34:
        raise ValueError('Incomplete/modified API output checks')
    if len(samples['samples']) < 2:
        raise ValueError('Missing serving GPU observations')
    bound_pids = {p['pid'] for p in result['processes']}
    for sample in samples['samples']:
        if (sample['gpu_index']!=0 or 'H200' not in sample['name'] or
                any(not math.isfinite(sample[k]) or sample[k]<0 for k in ['unix','memory_used_mib','memory_total_mib','utilization_percent'])
                or sample['memory_used_mib']>sample['memory_total_mib'] or sample['utilization_percent']>100
                or any(p['pid'] not in bound_pids for p in sample['owned_compute_processes'])):
            raise ValueError('GPU sample ownership/value differs')
    if (not any(s['owned_compute_processes'] for s in samples['samples']) or
            samples['peak_observed_mib']!=max(s['memory_used_mib'] for s in samples['samples'])):
        raise ValueError('No actual owned GPU observation')
    return dict(status='passed_actual_final50k_vllm_http',source_binding=admitted['source_binding'],
        manifest_sha256=admitted['manifest_sha256'],http_checks_sha256=sha256_file(root/'http_checks.json'),
        server_execution_sha256=sha256_file(root/'server_execution.json'),gpu_peak_observed_mib=samples['peak_observed_mib'],
        execution_proofs={n:sha256_file(root/n) for n in ['server/handle.json','server/result.json','server_exit.json',
            'listener_verified.json','http_complete.json','gpu_samples.json','resource_peak.json']},
        actual_api_verified=True,served_only_on_loopback=True,server_stopped=True,
        natural_eos_quality_proven=False,quality_success_asserted=False,goal_complete=False)


def controller(root,c):
    from scripts.evaluate_final_rehearsal import acquire_idle_gpu
    from scripts.tiered_checkpoint_retention import size_tree
    if (root/'.done').exists():
        result=verify_result(root,c); report=json.loads((root/'report.json').read_text())
        if (json.loads((root/'.done').read_text())!=dict(report_sha256=sha256_file(root/'report.json'),contract_sha256=sha256_file(root/'contract.json'))
                or any(report.get(k)!=v for k,v in result.items())):
            raise ValueError('Serving completion proof changed')
        return report
    reject_previous(root); (root/'pid').write_text(str(os.getpid())+'\n'); started=time.monotonic()
    def notify(case):
        if time.monotonic()-started>c['wait_seconds']:
            raise RuntimeError('Serving dependency wait budget exceeded')
        atomic_json(root/'state.json',dict(phase='running',case=case,goal_complete=False))
    try:
        while not dependency_ready(c): notify('await_final_offline_runtime'); time.sleep(c['poll_seconds'])
        authenticate_sources(c)
        with acquire_idle_gpu(notify):
            hw,capacity=admission(root,c)
            atomic_json(root/'execution_hardware.json',hw); atomic_json(root/'execution_capacity.json',capacity)
            admitted=authenticate_sources(c); atomic_json(root/'admitted.json',admitted)
            atomic_json(root/'execution_started.json',dict(controller_pid=os.getpid(),contract_id=identity(c)))
            local=exact_path(c['local_root'])/c['local_subdirectory']; local.mkdir(exist_ok=False)
            for name in ['tmp','cache']: (local/name).mkdir()
            env=dict(os.environ,CUDA_VISIBLE_DEVICES='0',OMP_NUM_THREADS='4',MKL_NUM_THREADS='4',OPENBLAS_NUM_THREADS='4',
                TOKENIZERS_PARALLELISM='false',RAYON_NUM_THREADS='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
                VLLM_NO_USAGE_STATS='1',DO_NOT_TRACK='1',VLLM_WORKER_MULTIPROC_METHOD='spawn',VLLM_USE_V1='1',
                TMPDIR=str(local/'tmp'),VLLM_CACHE_ROOT=str(local/'cache'),TORCHINDUCTOR_CACHE_DIR=str(local/'cache'))
            last=[-30]; begin=time.monotonic(); measured=dict(combined_peak_pss_bytes=0)
            def guard(pid,elapsed,peak):
                from scripts.process_accounting import parse_rollup
                combined=peak+parse_rollup(Path('/proc/self/smaps_rollup').read_text())['Pss']
                measured['combined_peak_pss_bytes']=max(measured['combined_peak_pss_bytes'],combined)
                if elapsed>c['max_seconds'] or combined>64*GIB or size_tree(root)>GIB:
                    raise RuntimeError('Serving PSS/time/proof budget exceeded')
                if elapsed-last[0]>=30:
                    _,capacity=admission(root,c); atomic_json(root/'current_capacity.json',capacity); last[0]=elapsed
            # This guard may run on the HTTP thread; keep it read-only. Main
            # thread supervision independently enforces the PSS/storage cap.
            def source_guard():
                import shutil
                if (time.monotonic()-begin>c['max_seconds'] or
                        shutil.disk_usage(root).free<200*GIB or shutil.disk_usage(c['local_root']).free<200*GIB):
                    raise RuntimeError('Serving source time/storage guard')
            package=authenticate_release(resolve(c['package']),expected_manifest_sha256=admitted['manifest_sha256'],
                expected_source_binding=admitted['source_binding'],expected_tokenizer_sha256=c['tokenizer_sha256'],
                guard=source_guard)
            notify('actual_localhost_api_checks')
            metrics=run_owned_http(root,c,env,package,guard)
            atomic_json(root/'resource_peak.json',measured)
        report=verify_result(root,c); report['supervision']=metrics
        atomic_json(root/'report.json',report)
        atomic_json(root/'.done',dict(report_sha256=sha256_file(root/'report.json'),contract_sha256=sha256_file(root/'contract.json')))
        atomic_json(root/'state.json',dict(phase='complete',case='local_api_verified_server_stopped',goal_complete=False))
        return report
    except BaseException as exc:
        atomic_json(root/'state.json',dict(phase='error',reason=repr(exc),goal_complete=False)); raise


def main(a):
    runtime=exact_path(resolve(a.runtime_run)); root=runtime.parent/'vllm_serving_v1'
    if a.prepare:
        if root.exists(): raise ValueError('Preserve previous serving namespace')
        c=make_contract(runtime)
        if 'final_storage' in c:
            from scripts.completed_budget_storage import admit_reference
            admit_reference(c['main_run'],c['final_storage'])
        hw,capacity=admission(root,c); root.mkdir()
        atomic_json(root/'contract.json',c); atomic_json(root/'preparation_hardware.json',hw)
        atomic_json(root/'preparation_capacity.json',capacity)
        atomic_json(root/'prepared.json',dict(contract_sha256=sha256_file(root/'contract.json'),actual_api_verified=False))
        return
    c=load_contract(root)
    with (root/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        def interrupted(_signum,_frame): raise InterruptedError('Serving controller interrupted')
        previous=signal.signal(signal.SIGTERM,interrupted)
        try: controller(root,c)
        finally: signal.signal(signal.SIGTERM,previous)


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime-run',default='artifacts/3b_base_50k_v1/vllm_runtime_v1')
    mode=p.add_mutually_exclusive_group(required=True);mode.add_argument('--prepare',action='store_true');mode.add_argument('--run',action='store_true')
    main(p.parse_args())
