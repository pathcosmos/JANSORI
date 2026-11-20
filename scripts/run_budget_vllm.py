"""Wait for verified final HF export, then supervise isolated one-GPU inference.

No training, package overwrite, deletion, upload or automatic failed retry.
Runtime evidence is inside the existing export100GiB reservation; local cache
uses the already reserved shared scratch pool. Base-model smoke, not benchmark.
"""
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve,artifact_path,sha256_file,identity,atomic_json
from inference.runtime_package import exact_path

SCRIPT='scripts/run_budget_vllm.py'
WORKER='inference/budget_vllm_worker.py'
GIB=2**30


def make_contract(export, environment, *, local_environment_cap_gib=64):
    if type(local_environment_cap_gib) is not int or local_environment_cap_gib not in (60,64):
        raise ValueError('Unreviewed local environment cap')
    from scripts.export_budget_hf import load_contract
    export=exact_path(resolve(export));ec=load_contract(export)
    environment=exact_path(resolve(environment));er=json.loads((environment/'report.json').read_text())
    imported=json.loads((environment/'import_report.json').read_text())
    if (er['status']!='prepared_isolated_vllm_cpu_only_gpu_pending' or
            er['import_report_sha256']!=sha256_file(environment/'import_report.json') or
            er['lock_sha256']!=sha256_file(environment/'requirements.lock')):
        raise ValueError('Unverified isolated environment preparation')
    local=exact_path(er['local_root']);root=export/'vllm_runtime_v1'
    source=ec['source'];mc=json.loads(resolve(source['main_run']).joinpath('contract.json').read_text())
    if not any(local.is_relative_to(exact_path(p)) for p in mc['shared_storage']['local_temp_paths']):
        raise ValueError('Runtime local cache outside shared scratch')
    names=[SCRIPT,WORKER,'inference/vllm_validation.py','inference/runtime_package.py',
           'scripts/horizon_probe_process.py','scripts/process_accounting.py',
           'scripts/run_budget_final_resume.py','scripts/export_budget_hf.py',
           'scripts/evaluate_final_rehearsal.py','scripts/resource_admission.py','scripts/extended_horizon_storage.py']
    paths=[export/'contract.json',export/'prepared.json',environment/'report.json',
           environment/'contract.json',environment/'requirements.lock',environment/'import_report.json']
    result=dict(version=1,purpose='final50k_vllm_runtime',root=artifact_path(root),export_root=artifact_path(export),
        package=artifact_path(export/'hf'),main_run=source['main_run'],tokenizer=source['tokenizer'],prompts=source['prompts'],
        tokenizer_sha256=sha256_file(resolve(source['tokenizer'])),venv=er['venv'],local_root=str(local),
        versions=imported['versions'],code={**ec['code'],**{n:sha256_file(resolve(n)) for n in names}},
        inputs={**ec['inputs'],**{artifact_path(p):sha256_file(p) for p in paths}},
        max_seconds=1800,max_pss_gib=64,durable_gib=1,local_environment_cap_gib=local_environment_cap_gib,
        cpu_affinity=list(range(8,16)),threads=4,poll_seconds=30,wait_seconds=22*86400,
        local_subdirectory='runtime_50k_v1',target_steps=50000,
        engine=dict(dtype='bfloat16',tensor_parallel_size=1,max_model_len=4096,max_num_seqs=12,
            max_num_batched_tokens=4096,gpu_memory_utilization=.25,enforce_eager=True,
            enable_prefix_caching=False,seed=0,trust_remote_code=False,generation_config='vllm'),
        goal_complete=False)
    if source.get('independent_runtime') is not None:result['independent_runtime']=source['independent_runtime']
    if 'final_storage' in source:result['final_storage']=source['final_storage']
    return result


def load_contract(root):
    from scripts.evaluate_final_rehearsal import check_bindings
    root=exact_path(resolve(root));c=json.loads((root/'contract.json').read_text())
    prepared=json.loads((root/'prepared.json').read_text())
    if (prepared['contract_sha256']!=sha256_file(root/'contract.json') or
            c['purpose']!='final50k_vllm_runtime' or c['target_steps']!=50000 or
            c['local_subdirectory']!='runtime_50k_v1' or exact_path(resolve(c['root']))!=root or
            root!=exact_path(resolve(c['export_root']))/'vllm_runtime_v1' or
            (c['max_seconds'],c['max_pss_gib'],c['durable_gib'])!=(1800,64,1) or
            type(c['local_environment_cap_gib']) is not int or c['local_environment_cap_gib'] not in (60,64)):
        raise ValueError('Unreviewed runtime contract')
    check_bindings(c)
    return c


def admission(root,c):
    from scripts.export_budget_hf import load_contract as export_contract,admission as export_admission
    from scripts.tiered_checkpoint_retention import size_tree
    export=resolve(c['export_root']);ec=export_contract(export)
    if c.get('final_storage')!=ec['source'].get('final_storage'):
        raise ValueError('Export/runtime separate storage binding differs')
    hw,capacity=export_admission(export,ec)
    local=local_capacity(c,capacity)
    if (size_tree(root)>GIB or
            hw['memory']['cache_aware_admission']['headroom_gib']<capacity['combined_memory_gib']+64):
        raise RuntimeError('Combined runtime memory/storage budget unavailable')
    if not set(c['cpu_affinity'])<=set(hw['cpu']['affinity']):
        raise RuntimeError('Runtime CPU affinity unavailable')
    return hw,dict(**capacity,**local,runtime_pss_gib=64,runtime_part_of_export_budget=True)


def local_capacity(c,capacity):
    from scripts.tiered_checkpoint_retention import size_tree
    cap=c['local_environment_cap_gib']
    if type(cap) is not int or cap not in (60,64):raise ValueError('Unreviewed local environment cap')
    used=size_tree(c['local_root']);total=capacity['local_temporary_used_bytes']
    projected=total-used+cap*GIB
    if used>cap*GIB or total<used or projected>1024*GIB:
        raise RuntimeError('Shared local pool cannot fit full runtime environment cap')
    return dict(runtime_environment_used_bytes=used,runtime_environment_cap_bytes=cap*GIB,
        projected_temporary_bytes_with_runtime=projected)


def dependency_ready(c):
    from scripts.export_budget_hf import dependency_ready
    return dependency_ready(c['export_root'],'export_budget_hf.py')


def authenticate_export(c):
    from scripts.export_budget_hf import load_contract as export_contract,controller
    export=resolve(c['export_root'])
    if not (export/'.done').is_file():raise ValueError('Final HF export is incomplete')
    # Existing controller's completed branch freshly rechecks payload, exact
    # source/diagnostic stats, contract bindings and actual process receipts.
    report=controller(export,export_contract(export))
    if report['source_binding']['step']!=50000:raise ValueError('Wrong final target')
    if report['source_binding'].get('independent_runtime')!=c.get('independent_runtime'):
        raise ValueError('HF/vLLM independent source runtime differs')
    if report['source_binding'].get('final_storage')!=c.get('final_storage'):
        raise ValueError('HF/vLLM separate storage binding differs')
    return dict(contract_id=identity(c),manifest_sha256=report['publication_manifest_sha256'],
        source_binding=report['source_binding'],export_report_sha256=sha256_file(export/'report.json'),
        export_marker_sha256=sha256_file(export/'.done'))


def reject_previous(root):
    root=resolve(root)
    for name in ('execution_started.json','admitted.json','supervision','worker_result.json','report.json','.done'):
        if (root/name).exists():raise RuntimeError('Preserve prior runtime attempt: '+name)
    # Reject orphan workers even if a controller died before writing its receipt.
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            args=(proc/'cmdline').read_bytes().decode().split('\0')
            if ('--run-dir' in args and resolve(args[args.index('--run-dir')+1])==root
                    and any(a.endswith(WORKER) for a in args)):
                raise RuntimeError('Existing runtime worker; do not duplicate')
        except (OSError,ValueError,IndexError):pass


def command(root,c):
    return ['taskset','-c',','.join(map(str,c['cpu_affinity'])),str(Path(c['venv'])/'bin/python'),'-u',str(resolve(WORKER)),'--run-dir',str(root)]


def verify_result(root,c):
    from scripts.evaluate_final_rehearsal import check_bindings
    from inference.vllm_validation import build_cases
    from tokenizers import Tokenizer
    check_bindings(c);admitted=authenticate_export(c)
    if json.loads((root/'admitted.json').read_text())!=admitted:raise ValueError('Runtime source admission changed')
    h=json.loads((root/'supervision/handle.json').read_text());execution=json.loads((root/'worker_execution.json').read_text())
    run=json.loads((root/'supervision/result.json').read_text());r=json.loads((root/'worker_result.json').read_text())
    if (run['exit_code']!=0 or run['remaining_owned_processes'] or run['owner']!=h['owner'] or
            run['launcher_pid']!=h['pid'] or run['command']!=h['command'] or
            h['command']!=command(root,c) or
            execution['pid']!=h['pid'] or execution['start_ticks']!=h['process']['start_ticks'] or
            execution['owner']!=h['owner'] or execution['contract_id']!=identity(c) or
            execution['admission_sha256']!=sha256_file(root/'admitted.json') or
            execution['package_manifest_sha256']!=admitted['manifest_sha256'] or
            execution['versions']!=c['versions'] or execution['cuda_build']!='12.8' or execution['visible_device_count']!=1 or 'H200' not in execution['device_name'] or
            r['status']!='passed_final50k_vllm_worker_pending_review' or r['actual_gpu_inference_executed'] is not True or
            r['execution_sha256']!=sha256_file(root/'worker_execution.json') or r['manifest_sha256']!=admitted['manifest_sha256'] or
            any(r[k] is not False for k in ('quality_success_asserted','ollama_verified','goal_complete')) or
            not math.isfinite(r['seconds']) or not 0<=r['seconds']<=c['max_seconds']):
        raise ValueError('Actual isolated runtime execution binding differs')
    raw=Tokenizer.from_file(str(resolve(c['tokenizer'])))
    prompts=[json.loads(s) for s in resolve(c['prompts']).read_text().splitlines() if s.strip()]
    cases=build_cases(raw,prompts)
    if r['cases']!=cases:raise ValueError('Runtime case coverage changed')
    checks=r['output_checks'];tokens=checks['token_cases'];texts=checks['text_cases']
    if (checks['status']!='passed_output_checks_execution_provenance_required' or len(tokens)!=12 or len(texts)!=10
            or checks['goal_complete'] is not False or checks['quality_benchmark'] is not False):
        raise ValueError('Output checks incomplete')
    for expected,row in zip(cases,tokens):
        ids=row['generated_ids']
        if (row['id']!=expected['id'] or row['prompt_tokens']!=len(expected['ids']) or
                len(ids)!=expected['generated'] or any(type(t) is not int or not 0<=t<65536 for t in ids) or
                row['generated_text']!=raw.decode(ids,skip_special_tokens=False) or row['finish_reason']!='length' or
                row['prompt_scored_targets']!=len(expected['ids'])-1 or
                not math.isfinite(row['prompt_nll_sum']) or row['prompt_nll_sum']<0 or
                not math.isfinite(row['generated_logprob_sum']) or row['generated_logprob_sum']>1e-6):
            raise ValueError('Runtime generated token/coverage evidence invalid')
    if any(a['id']!=b['id'] or a['generated_ids']!=b['generated_ids'] for a,b in zip(tokens[:10],texts)):
        raise ValueError('Text/token runtime results differ')
    return dict(status='passed_actual_final50k_vllm_inference',source_binding=admitted['source_binding'],
        manifest_sha256=admitted['manifest_sha256'],worker_result_sha256=sha256_file(root/'worker_result.json'),
        actual_gpu_inference_verified=True,quality_success_asserted=False,ollama_verified=False,goal_complete=False)


def controller(root,c):
    from scripts.evaluate_final_rehearsal import acquire_idle_gpu
    from scripts.run_budget_final_resume import supervise_command
    from scripts.tiered_checkpoint_retention import size_tree
    if (root/'.done').exists():
        r=verify_result(root,c);m=json.loads((root/'.done').read_text());report=json.loads((root/'report.json').read_text())
        if m!=dict(report_sha256=sha256_file(root/'report.json'),contract_sha256=sha256_file(root/'contract.json')) or any(report[k]!=v for k,v in r.items()):
            raise ValueError('Runtime completion evidence differs')
        return report
    reject_previous(root);(root/'pid').write_text(str(os.getpid())+'\n');started=time.monotonic()
    def notify(case):
        if time.monotonic()-started>c['wait_seconds']:raise RuntimeError('Runtime wait budget exceeded')
        atomic_json(root/'state.json',dict(phase='running',case=case,goal_complete=False))
    try:
        while not dependency_ready(c):notify('await_final_hf_export');time.sleep(c['poll_seconds'])
        notify('authenticate_final_hf');authenticate_export(c)
        with acquire_idle_gpu(notify):
            hw,capacity=admission(root,c)
            atomic_json(root/'execution_hardware.json',hw);atomic_json(root/'execution_capacity.json',capacity)
            atomic_json(root/'admitted.json',authenticate_export(c))
            atomic_json(root/'execution_started.json',dict(controller_pid=os.getpid(),contract_id=identity(c)))
            local=exact_path(c['local_root'])/c['local_subdirectory']
            local.mkdir(exist_ok=False)
            for name in ('tmp','cache'):(local/name).mkdir()
            env=dict(os.environ,CUDA_VISIBLE_DEVICES='0',OMP_NUM_THREADS='4',MKL_NUM_THREADS='4',OPENBLAS_NUM_THREADS='4',
                TOKENIZERS_PARALLELISM='false',RAYON_NUM_THREADS='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
                VLLM_NO_USAGE_STATS='1',DO_NOT_TRACK='1',VLLM_WORKER_MULTIPROC_METHOD='spawn',VLLM_USE_V1='1',
                TMPDIR=str(local/'tmp'),VLLM_CACHE_ROOT=str(local/'cache'),TORCHINDUCTOR_CACHE_DIR=str(local/'cache'))
            cmd=command(root,c)
            last=[-30];notify('actual_vllm_inference')
            def guard(pid,elapsed,peak):
                if elapsed>c['max_seconds'] or peak>64*GIB or size_tree(root)>GIB:
                    raise RuntimeError('Runtime worker budget exceeded')
                if elapsed-last[0]>=30:
                    _,capacity=admission(root,c);atomic_json(root/'current_capacity.json',capacity);last[0]=elapsed
            metrics=supervise_command(cmd,root=root,env=env,guard=guard)
        report=verify_result(root,c);report['supervision']=metrics
        atomic_json(root/'report.json',report)
        atomic_json(root/'.done',dict(report_sha256=sha256_file(root/'report.json'),contract_sha256=sha256_file(root/'contract.json')))
        atomic_json(root/'state.json',dict(phase='complete',case='vllm_inference_verified',goal_complete=False))
        return report
    except BaseException as exc:
        atomic_json(root/'state.json',dict(phase='error',reason=repr(exc),goal_complete=False));raise


def main(a):
    root=exact_path(resolve(a.export_run))/'vllm_runtime_v1'
    if a.prepare:
        if root.exists():raise ValueError('Preserve existing runtime namespace')
        c=make_contract(a.export_run,a.environment,local_environment_cap_gib=getattr(a,'local_environment_cap_gib',64))
        if 'final_storage' in c:
            from scripts.completed_budget_storage import admit_reference
            admit_reference(c['main_run'],c['final_storage'])
        hw,capacity=admission(root,c)
        root.mkdir();atomic_json(root/'contract.json',c)
        atomic_json(root/'preparation_hardware.json',hw);atomic_json(root/'preparation_capacity.json',capacity)
        atomic_json(root/'prepared.json',dict(contract_sha256=sha256_file(root/'contract.json'),actual_gpu_inference_verified=False))
        return
    c=load_contract(root)
    with (root/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        def stop(*_):raise InterruptedError('Runtime controller interrupted; preserve evidence')
        signal.signal(signal.SIGTERM,stop)
        controller(root,c)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--export-run',default='artifacts/3b_base_50k_v1')
    p.add_argument('--environment',default='runs/main_training_35k_plan_v1/serving_environment_v2')
    p.add_argument('--local-environment-cap-gib',type=int,choices=[60,64],default=64)
    m=p.add_mutually_exclusive_group(required=True);m.add_argument('--prepare',action='store_true');m.add_argument('--run',action='store_true')
    main(p.parse_args())
