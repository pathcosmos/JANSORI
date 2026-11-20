"""Prepare late CPU inputs, then evaluate only the audited completed50k horizon3B.

Uses the unchanged native FP32 evaluator and fixed raw holdout. No training,
checkpoint mutation, pruning, official benchmark, SFT or upload. Preparation
and the tiny CPU tests are not actual final3B evaluation evidence.
"""
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import PROJECT_ROOT
from common.data_contracts import resolve, artifact_path, atomic_json, sha256_file, identity
from scripts.run_native_evaluation import certified
from scripts.evaluate_final_main import verify_result
from scripts.audit_budget_training import TARGET_STEPS, TOTAL_TOKENS
FINAL_CHECKPOINT = f"ckpt_{TARGET_STEPS:07d}"

LATE_START_STEP = TARGET_STEPS - 500
from scripts.evaluate_final_rehearsal import check_bindings, acquire_idle_gpu

GIB=2**30


def process_live(pid, token, root):
    try:
        p=Path('/proc')/str(pid)
        return ((p/'stat').read_text().split(') ',1)[1].split()[0]!='Z' and
                token.encode() in (p/'cmdline').read_bytes() and str(root).encode() in (p/'cmdline').read_bytes())
    except FileNotFoundError:
        return False


def main_observation(root,independent_runtime=None):
    if independent_runtime is not None:
        from scripts.budget_independence_unavailable import observation
        return observation(root,independent_runtime)
    root=resolve(root)
    if (root/'.done').exists():
        return dict(complete=True,ready=True,step=TARGET_STEPS,scope='Completion marker triggers full audit; marker alone is not acceptance.')
    state=json.loads((root/'state.json').read_text())
    if state.get('phase') in ('error','failed','paused'):
        raise RuntimeError('Main training requires recovery; preserve evaluation preparation')
    pid=int((root/'pid').read_text());worker=state.get('worker_pid')
    from scripts.observe_budget_training import process_identity
    supervisor=process_identity(pid,'supervisor',root)
    launcher=process_identity(worker,'worker',root)
    if supervisor is None or launcher is None:
        if (root/'.done').exists():
            return dict(complete=True,ready=True,step=TARGET_STEPS)
        raise RuntimeError('Actual main controller/worker handle missing; inspect, never restart from this evaluator')
    path=root/'training/log.jsonl'
    raw=path.read_bytes() if path.exists() else b''
    lines=raw.splitlines()
    if raw and not raw.endswith(b'\n'):
        lines=lines[:-1]
    rows=[json.loads(line) for line in lines if line.strip()]
    train=[r for r in rows if 'loss' in r]
    step=train[-1]['step'] if train else 0
    if type(step) is not int or not 0<=step<=TARGET_STEPS:
        raise ValueError('Main observed step outside contract')
    return dict(complete=False,ready=step>=LATE_START_STEP,step=step,
                process_identities=dict(supervisor=supervisor,launcher=launcher),
                scope='CPU prewarm begins at49500; GPU scoring requires complete50000 audit and idle GPU lock.')


def completed_main(root,independent_runtime=None):
    if independent_runtime is not None:
        from scripts.budget_independence_unavailable import completed_source
        return completed_source(root,independent_runtime)
    from scripts.audit_budget_training import audit_run
    audit=audit_run(root,TARGET_STEPS)
    if (audit['validation_source'] or audit['counts']['committed_tokens']!=TOTAL_TOKENS or
            audit['counts']['successful_target_ranks']!=list(range(8))):
        raise ValueError('Final3B requires actual full8rank target coverage')
    checkpoint=resolve(root)/'training'/FINAL_CHECKPOINT
    proof=json.loads(resolve(audit['checkpoint_certificate']).read_text())
    meta=json.loads((checkpoint/'meta.json').read_text())
    if meta['config']['model']!='3b' or meta['step']!=TARGET_STEPS:
        raise ValueError('Final3B model/step identity differs')
    return checkpoint,proof,meta,audit


def make_contract(main_run,reference_run,*,runtime_control=None,storage_control=None):
    if runtime_control is not None and storage_control is not None:
        raise ValueError('Choose exactly one final source/storage route')
    main=resolve(main_run);c=json.loads((main/'contract.json').read_text())
    from scripts.supervise_budget_training import require_authorization
    require_authorization(c)
    config=json.loads((main/'main.json').read_text())
    if c['configurations']['main.json']!=sha256_file(main/'main.json') or config['model']!='3b' or config['max_steps']!=TARGET_STEPS:
        raise ValueError('Unreviewed main evaluation target')
    from train.budget_horizon_handoff import validate_configuration,load_handoff
    validate_configuration(config,c)
    independent=None
    if runtime_control is None:load_handoff(main,c,config)
    else:
        from scripts.budget_independence_unavailable import bind_source
        independent=bind_source(main,runtime_control)
    for path,digest in {**c['code'],**c['evidence']}.items():
        if sha256_file(resolve(path))!=digest:
            raise ValueError('Bound main evidence changed: '+path)
    ref,rc=certified(reference_run,'passed_actual_trained_native_evaluation')
    reference=resolve(reference_run)/ref['result']
    if sha256_file(reference)!=ref['result_sha256']:
        raise ValueError('Reference final1B native result changed')
    check_bindings(rc)
    old=json.loads(reference.read_text())
    if old['contract']['tokenizer_sha256']!=sha256_file(resolve(c['tokenizer'])):
        raise ValueError('Main/reference tokenizer differs')
    files=[main/'contract.json',main/'main.json',resolve(c['retention_authorization']),
           resolve(c['retention_revision']['authorization']),
           resolve(reference_run)/'contract.json',resolve(reference_run)/'report.json',resolve(reference_run)/'.done',reference]
    code={**rc['code'],**c['code'],**{p:sha256_file(resolve(p)) for p in
         ['scripts/evaluate_budget_native.py','scripts/evaluate_final_main.py','scripts/audit_budget_training.py', 'scripts/budget_final_lineage.py','scripts/audit_horizon_training.py','scripts/audit_main_training.py',
          'scripts/extended_horizon_storage.py','scripts/observe_budget_training.py',
          'scripts/horizon_process_handoff.py']}}
    inputs={**rc['inputs'],**c['evidence'],**{artifact_path(p):sha256_file(p) for p in files}}
    if independent is not None:
        code.update(independent['code']);inputs.update(independent['inputs'])
    result=dict(version=1,purpose='final_native_main_evaluation',main_run=artifact_path(main),
        evaluation=rc['evaluation'],reference_report=artifact_path(reference),tokenizer=c['tokenizer'],
        tokenizer_sha256=old['contract']['tokenizer_sha256'],samples=rc['samples'],holdout_manifest=rc['holdout_manifest'],
        expected_targets={k:v['valid_tokens'] for k,v in old['languages'].items()},code=code,inputs=inputs,framework=c['framework'],
        late_start_step=LATE_START_STEP,final_step=TARGET_STEPS,max_seconds=22*86400,worker_max_seconds=8*3600,
        host_rss_gib=256,local_budget_gib=8,durable_budget_gib=8,reserve_gib=200,
        cpu_affinity=list(range(64)),sqlite_cache_mib=131072,hash_workers=32,
        scope='Only completed3B50000 native FP32 fixed-holdout ko/en PPL and6generations. No model update, official benchmark or quality-success assertion.')
    if independent is not None:result['independent_runtime']=independent['reference']
    if storage_control is not None:
        from scripts.completed_budget_storage import bind
        storage=bind(main,storage_control)
        result['final_storage']=storage['reference']
        result['code'].update(storage['code']);result['inputs'].update(storage['inputs'])
        result['late_start_step']=TARGET_STEPS
    return result


def reserved_pool(contract,root):
    """Reject deferred16MiB budgets before any final-stage output or work."""
    from scripts.tiered_checkpoint_retention import exact_path
    root=exact_path(root)
    main=exact_path(resolve(contract['main_run']))
    mc=json.loads((main/'contract.json').read_text())
    if 'final_storage' in contract:
        if 'independent_runtime' in contract:
            raise ValueError('Mixed final source/storage routes')
        from scripts.completed_budget_storage import view
        mc=view(main,contract['final_storage'])
    pool=mc['shared_storage']['nfs_pools'].get('final_native')
    if (not pool or pool['paths']!=[str(root)] or
            pool['budget_bytes']!=contract['durable_budget_gib']*GIB):
        raise ValueError('Final50k native exact8GiB pool must be restored before setup or execution')
    return main,mc


def admission(contract,root):
    from scripts.resource_admission import inspect
    from scripts.tiered_checkpoint_retention import size_tree, exact_path
    from scripts.extended_horizon_storage import enforce_shared_space
    root=exact_path(root);main,mc=reserved_pool(contract,root);hw=inspect();p=mc['tiered_retention']
    combined=mc['max_pss_gib']+contract['host_rss_gib']
    if hw['gpus']!=mc['gpu_identity'] or hw['memory']['cache_aware_admission']['headroom_gib']<combined:
        raise RuntimeError('Final eval256GiB plus main future peak headroom unavailable')
    # Main combined reservations ALREADY contain this exact evaluation run.
    # Do not add its8GiB a second time or omit preserved diagnostic artifacts.
    admission=enforce_shared_space(main,mc)
    scratch=exact_path(Path(p['temp_root'])/'final_native')
    if str(exact_path(p['temp_root'])) not in mc['shared_storage']['local_temp_paths']:
        raise ValueError('Final native scratch absent from shared temporary pool')
    if Path(p['temp_root']).parent.stat().st_dev!=hw['storage']['scratch_parent']['device']:
        raise ValueError('Reviewed final evaluation temporary filesystem differs')
    used=size_tree(scratch)
    if (used>contract['local_budget_gib']*GIB or
            admission['local_temporary_used_bytes']-used+contract['local_budget_gib']*GIB>1024*GIB):
        raise RuntimeError('Final evaluator shared temporary pool unavailable')
    return hw,scratch

def reject_live_orphan(root):
    path=root/'worker_state.json'
    if path.exists():
        pid=json.loads(path.read_text()).get('pid')
        if pid and process_live(pid,'evaluate_budget_native.py',root):
            raise RuntimeError('Existing final3B evaluation worker still live; preserve before controller recovery')


def worker(root):
    root=resolve(root);c=json.loads((root/'contract.json').read_text());check_bindings(c)
    if 'final_storage' in c:
        from scripts.completed_budget_storage import admit_reference
        admit_reference(c['main_run'],c['final_storage'])
    admission(c,root)
    def notify(phase,**kwargs):
        atomic_json(root/'worker_state.json',dict(phase=phase,pid=os.getpid(),unix=time.time(),**kwargs))
    import torch
    import pyarrow as pa
    from tokenizers import Tokenizer
    from eval.prepare_holdout import local_verified_inputs
    from data.split_inputs import verify_prepared
    from eval.run import run,records
    if c['framework']!=dict(torch=str(torch.__version__),cuda=torch.version.cuda):
        raise ValueError('Final evaluator framework changed')
    pa.set_cpu_count(4);pa.set_io_thread_count(4)
    e=c['evaluation'];started=time.monotonic();notify('verify_fresh_local_eval_context')
    with local_verified_inputs(e['source_manifest'],e['canonical_split_manifest'],e['reader_manifest'],
                              cache_mib=c['sqlite_cache_mib'],hash_workers=c['hash_workers']) as inputs:
        prepared=verify_prepared(c['holdout_manifest'],'eval',c['samples'],verified_inputs=inputs)
        tokenizer=Tokenizer.from_file(str(resolve(c['tokenizer'])))
        samples=records(resolve(c['samples']))
        expected={k:sum(len(tokenizer.encode(r['text'],add_special_tokens=False).ids) for r in samples if r['category']==k) for k in ['ko','en']}
        if expected!=c['expected_targets']:
            raise ValueError('Fresh raw-text native target counts changed')
        atomic_json(root/'fresh_context.json',dict(seconds=time.monotonic()-started,prepared_contract_id=prepared['contract_id'],
                    expected_targets=expected,scope='Fresh physical verification in this live process; no persistent skip-validation cache.'))
        while not (resolve(c['main_run'])/'.done').exists():
            observation=main_observation(c['main_run'],c.get('independent_runtime'));notify('await_main_final_checkpoint',**observation);time.sleep(30)
        notify('audit_main_actual_target')
        checkpoint,proof,meta,audit=completed_main(c['main_run'],c.get('independent_runtime'))
        atomic_json(root/'final_training_audit.json',audit)
        if meta['training_contract']['mix']['tokenizer_sha256']!=c['tokenizer_sha256']:
            raise ValueError('Final main checkpoint tokenizer changed')
        check_bindings(c)
        with acquire_idle_gpu(notify):
            os.environ['CUDA_VISIBLE_DEVICES']='0';notify('native_final3b_evaluation');started=time.monotonic()
            result=run(checkpoint,resolve(c['tokenizer']),resolve(c['samples']),resolve(e['prompts']),root/'3b_final.json',
                seq_len=4096,stride=2048,max_new_tokens=64,seed=e['seed'],temperature=0,device='cuda',
                holdout_manifest=resolve(c['holdout_manifest']),verified_inputs=inputs)
            reference=json.loads(resolve(c['reference_report']).read_text())
            verify_result(result,proof,tokenizer,expected,reference,expected_step=TARGET_STEPS,expected_device='cuda')
            seconds=time.monotonic()-started
    check_bindings(c)
    report=dict(status='passed_actual_final3b_native_evaluation',result='3b_final.json',result_sha256=sha256_file(root/'3b_final.json'),
        final_training_audit_sha256=sha256_file(root/'final_training_audit.json'),main_report_sha256=sha256_file(resolve(c['main_run'])/'report.json'),
        languages=result['languages'],aggregate=result['aggregate'],elapsed_evaluation_seconds=seconds,
        scope='Actual final3B native evaluation execution/coverage/content passed. Generated quality review,8GPU final restore and retention audit remain separate. Fixed1466document sample, not all holdout or official benchmarks.')
    atomic_json(root/'report.json',report)
    atomic_json(root/'.done',dict(report_sha256=sha256_file(root/'report.json'),contract_sha256=sha256_file(root/'contract.json')))
    notify('complete')


def main(a):
    if a.worker:
        return worker(a.run_dir)
    from scripts.run_local_split import process_tree_rss,size_tree,stop
    root=resolve(a.run_dir);root.relative_to(resolve('runs'))
    if root.is_relative_to(resolve(a.main_run)):
        raise ValueError('Evaluation must use an independent run')
    # The source currently reserves metadata only. Creating/launching the final
    # stage is forbidden until its phase budget has been restored and admitted.
    c=make_contract(a.main_run,a.reference_run,runtime_control=getattr(a,'runtime_control',None),
                    storage_control=getattr(a,'storage_control',None))
    if 'final_storage' in c:
        from scripts.completed_budget_storage import admit_reference
        admit_reference(c['main_run'],c['final_storage'])
    admission(c,root)
    root.mkdir(parents=True,exist_ok=True)
    with (root/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        check_bindings(c)
        cp=root/'contract.json'
        if cp.exists() and json.loads(cp.read_text())!=c:
            raise ValueError('Final evaluator contract changed; use a new independent run')
        if not cp.exists():
            atomic_json(cp,c)
        if a.prepare:
            atomic_json(root/'prepared.json',dict(status='prepared_not_executed',contract_sha256=sha256_file(cp),
                command=[sys.executable,'scripts/evaluate_budget_native.py','--run-dir',artifact_path(root),'--main-run',a.main_run,'--reference-run',a.reference_run,*(['--runtime-control',a.runtime_control] if getattr(a,'runtime_control',None) else []),*(['--storage-control',a.storage_control] if getattr(a,'storage_control',None) else []),'--run'],
                scope=('Separate storage route: completed50000 audit required before any stage output/work.'
                       if 'final_storage' in c else 'Preparation only; waits for main49500 before CPU work and audited50000 before GPU scoring.')))
            return
        if (root/'.done').exists():
            return certified(root,'passed_actual_final3b_native_evaluation')
        reject_live_orphan(root);check_bindings(c)
        (root/'pid').write_text(str(os.getpid())+'\n')
        started=time.monotonic();proc=None;peak=0;attempt=str(time.time_ns())
        try:
            while True:
                observation=main_observation(c['main_run'],c.get('independent_runtime'))
                if observation['ready']:
                    break
                atomic_json(root/'state.json',dict(phase='running',case='await_late_main',**observation))
                if time.monotonic()-started>c['max_seconds']:
                    raise RuntimeError('Final evaluation late-trigger time budget')
                time.sleep(30)
            hw,scratch=admission(c,root);atomic_json(root/('hardware_'+attempt+'.json'),hw)
            scratch.mkdir(parents=True,exist_ok=True)
            cpus=sorted(set(c['cpu_affinity'])&set(hw['cpu']['affinity']))
            if not cpus:
                raise RuntimeError('Final evaluation CPU affinity unavailable')
            env=dict(os.environ,OMP_NUM_THREADS='2',NUMEXPR_NUM_THREADS='2',RAYON_NUM_THREADS='8',TMPDIR=str(scratch),SQLITE_TMPDIR=str(scratch),PYTHONUNBUFFERED='1')
            command=['taskset','-c',','.join(map(str,cpus)),sys.executable,'-u',str(Path(__file__).resolve()),'--run-dir',str(root),'--worker']
            worker_start=time.monotonic()
            with (root/('console_'+attempt+'.log')).open('x') as log:
                proc=subprocess.Popen(command,cwd=PROJECT_ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            last_admission=0.
            while proc.poll() is None:
                if time.monotonic()-last_admission>=30:
                    check_bindings(c)
                    admission(c,root)
                    last_admission=time.monotonic()
                peak=max(peak,process_tree_rss(os.getpid()))
                w=json.loads((root/'worker_state.json').read_text()) if (root/'worker_state.json').exists() else {}
                atomic_json(root/'state.json',dict(phase='running',case=w.get('phase','starting'),worker_pid=proc.pid,peak_rss_bytes=peak,elapsed_seconds=time.monotonic()-started))
                if peak>c['host_rss_gib']*GIB or time.monotonic()-worker_start>c['worker_max_seconds']:
                    raise RuntimeError('Final evaluator worker RSS/time budget')
                for path,budget in [(root,c['durable_budget_gib']),(scratch,c['local_budget_gib'])]:
                    if size_tree(path)>budget*GIB or shutil.disk_usage(path).free<c['reserve_gib']*GIB:
                        raise RuntimeError('Final evaluator disk/reserve budget')
                time.sleep(2)
            if proc.returncode:
                raise RuntimeError('Final3B evaluator worker failed; preserve evidence and inspect console')
            report,_=certified(root,'passed_actual_final3b_native_evaluation')
            if report['result_sha256']!=sha256_file(root/report['result']):
                raise ValueError('Final result content changed')
            atomic_json(root/'state.json',dict(phase='complete',peak_rss_bytes=peak))
        except BaseException as exc:
            if proc is not None and proc.poll() is None:
                stop(proc)
            atomic_json(root/'state.json',dict(phase='error',error=str(exc)))
            raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',required=True)
    p.add_argument('--main-run',default='runs/3b_full_50k_v1')
    p.add_argument('--reference-run',default='runs/rehearsal_final_native_v1')
    p.add_argument('--runtime-control',help='Explicit accepted independent runtime; never inferred from main files')
    p.add_argument('--storage-control',help='Explicit separate final reservations; requires completed50000 before setup')
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare',action='store_true');mode.add_argument('--run',action='store_true');mode.add_argument('--worker',action='store_true')
    main(p.parse_args())
