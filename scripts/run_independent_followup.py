"""Serial final57k diagnostics/export with durable, non-retrying stage handles.

Preparation requires the accepted independent runtime, restored reservations
and retired parent proofs. Existing controllers enforce their GPU/resource
gates. Failure preserves launched children and evidence for explicit review;
this manager never stops main training, deletes checkpoints or uploads files.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve,sha256_file
from scripts.tiered_checkpoint_retention import exact_path,atomic_json,size_tree
from scripts.independent_runtime_view import RuntimeView,json_file
from scripts.independent_parent_retention import verify_retired_parents
from scripts.audit_checkpoint_retention import Snapshot
from scripts.extended_horizon_storage import enforce_shared_space
from scripts.horizon_probe_process import OwnedCommand,OWNER_ENV,tagged_pids
from scripts.horizon_process_handoff import process_record
from scripts.evaluate_final_rehearsal import check_bindings

OWN='scripts/run_independent_followup.py'
CAP=32*2**20
ENVIRONMENT='runs/main_training_35k_plan_v1/serving_environment_v2'


def future_local_capacity(contract,space):
    temp=exact_path(contract['tiered_retention']['temp_root'])
    resume=Path(contract['shared_storage']['nfs_pools']['final_resume']['paths'][0]).name
    environment=exact_path(json_file(resolve(ENVIRONMENT)/'report.json')['local_root'])
    if not any(environment.is_relative_to(exact_path(p)) for p in contract['shared_storage']['local_temp_paths']):
        raise ValueError('Serving environment outside accounted local temporary pool')
    sizes=[size_tree(p) for p in [temp/'final_native',temp/resume,environment]]
    total=space['local_temporary_used_bytes'];projected=total-sum(sizes)+(8+192+60+1)*2**30
    if (total<sum(sizes) or any(used>cap*2**30 for used,cap in zip(sizes,[8,192,60])) or projected>1024*2**30):
        raise RuntimeError('Final diagnostics and60GiB environment exceed shared1024GiB peak')
    return dict(projected_final_temporary_bytes=projected,runtime_environment_cap_gib=60,
        native_resume_environment_used_bytes=sizes,extra_margin_gib=1)


def coordinator_admission(view):
    from scripts.resource_admission import inspect
    hw=inspect();contract=json_file(view.main/'contract.json')
    # Reserve the original producer and the largest serial child as well as
    # this coordinator. Cache-aware headroom preserves the existing RAM reserve.
    required=contract['max_pss_gib']+256+16
    if (hw['gpus']!=contract['gpu_identity'] or len(hw['gpus'])!=8 or hw['cpu']['effective']<2 or
            hw['memory']['cache_aware_admission']['headroom_gib']<required):
        raise RuntimeError('Final coordinator/producer/child hardware budget unavailable')
    space=enforce_shared_space(view.main,contract)
    return dict(hardware=hw,combined_memory_gib=required,coordinator_pss_gib=16,
        capacity=space,future_local=future_local_capacity(contract,space))


def stages(view):
    contract=json_file(view.main/'contract.json');pools=contract['shared_storage']['nfs_pools']
    paths={}
    for key,gib in [('final_native',8),('final_resume',1),('final_retention',1),('hf_and_serving',100)]:
        pool=pools[key]
        if len(pool['paths'])!=1 or pool['budget_bytes']!=gib*2**30:
            raise ValueError('Exact restored final reservation required: '+key)
        paths[key]=str(exact_path(pool['paths'][0]))
    native,resume,retention,export=[paths[k] for k in ['final_native','final_resume','final_retention','hf_and_serving']]
    runtime=str(Path(export)/'vllm_runtime_v1');serving=str(Path(export)/'vllm_serving_v1')
    ref=['--runtime-control',str(view.control)];main=['--main-run',str(view.main)]
    definitions=[
        ('native_prepare','evaluate_restored_native.py',native,['--run-dir',native,*main,*ref,'--prepare']),
        ('native','evaluate_restored_native.py',native,['--run-dir',native,*main,*ref,'--run']),
        ('resume_plan','prepare_restored_final_resume.py',resume,['--source-run',str(view.main),'--run-dir',resume,'--native-run',native,*ref]),
        ('resume_prepare','run_restored_final_resume.py',resume,['--run-dir',resume,'--prepare']),
        ('resume','run_restored_final_resume.py',resume,['--run-dir',resume,'--run']),
        ('retention_prepare','run_restored_retention_audit.py',retention,['--run-dir',retention,*main,'--native-run',native,'--resume-run',resume,*ref,'--prepare']),
        ('retention','run_restored_retention_audit.py',retention,['--run-dir',retention,'--run']),
        ('hf_prepare','export_restored_hf.py',export,['--run-dir',export,*main,'--native-run',native,'--resume-run',resume,'--retention-run',retention,*ref,'--prepare']),
        ('hf','export_restored_hf.py',export,['--run-dir',export,'--run']),
        ('vllm_prepare','run_restored_vllm.py',runtime,['--export-run',export,'--environment',str(resolve(ENVIRONMENT)),
            '--local-environment-cap-gib','60','--prepare']),
        ('vllm','run_restored_vllm.py',runtime,['--export-run',export,'--run']),
        ('serving_prepare','run_restored_serving.py',serving,['--runtime-run',runtime,'--prepare']),
        ('serving','run_restored_serving.py',serving,['--runtime-run',runtime,'--run'])]
    return [dict(name=n,output=p,command=[sys.executable,'-u',str(resolve('scripts/'+script)),*args])
            for n,script,p,args in definitions]


def prepare(control):
    view=RuntimeView(control);root=view.control/'followup_v1'
    if view.binding['code'].get(OWN)!=sha256_file(resolve(OWN)):
        raise ValueError('Final manager not bound to accepted runtime')
    from scripts.independent_final_source import observation
    reference=dict(control_dir=str(view.control),runtime_contract_sha256=view.digest)
    observed=observation(view.main,reference)
    if observed.get('phase') in ('await_independent_execution','await_all8_consumption'):
        raise ValueError('Actual running/complete independent source required')
    verify_retired_parents(view,Snapshot())
    rows=stages(view)
    if root.exists() or any(Path(row['output']).exists() for row in rows):
        raise ValueError('Preserve existing followup/output namespaces')
    contract=json_file(view.main/'contract.json');admission=coordinator_admission(view)
    pool=exact_path(contract['shared_storage']['nfs_pools']['transition']['paths'][0])
    if not root.is_relative_to(pool) or size_tree(pool)+CAP>2**30:
        raise ValueError('Final manager metadata outside available transition budget')
    # Freeze existing implementation files before any child creates its own
    # contract; subsequent preparation must not silently bind changed code.
    code={str(p.relative_to(resolve('.'))):sha256_file(p)
          for folder in ['scripts','train','inference','model','common','eval']
          for p in resolve(folder).rglob('*.py')}
    inputs={str(view.path):view.digest,str(view.main/'contract.json'):sha256_file(view.main/'contract.json')}
    inputs.update({str(resolve(ENVIRONMENT)/name):sha256_file(resolve(ENVIRONMENT)/name)
        for name in ['report.json','contract.json','requirements.lock','import_report.json']})
    plan=dict(purpose='independent_final57k_followup',control=str(view.control),root=str(root),
        main_run=str(view.main),reference=reference,stages=rows,code=code,inputs=inputs,
        max_seconds=22*86400,metadata_bytes=CAP,max_pss_gib=16,poll_seconds=5,automatic_retry=False,
        goal_complete=False,remaining_after_stages=['quality review','Ollama compatibility conclusion','final Korean report'])
    root.mkdir();atomic_json(root/'plan.json',plan)
    atomic_json(root/'preparation_admission.json',admission)
    atomic_json(root/'prepared.json',dict(plan_sha256=sha256_file(root/'plan.json'),execution_started=False))
    return plan


def load(control):
    view=RuntimeView(control);root=view.control/'followup_v1';plan=json_file(root/'plan.json')
    if (json_file(root/'prepared.json')!=dict(plan_sha256=sha256_file(root/'plan.json'),execution_started=False) or
            plan.get('purpose')!='independent_final57k_followup' or plan['root']!=str(root) or
            plan['main_run']!=str(view.main) or plan['control']!=str(view.control) or
            plan['reference']!=dict(control_dir=str(view.control),runtime_contract_sha256=view.digest) or
            plan['stages']!=stages(view) or plan['max_seconds']!=22*86400 or
            plan['metadata_bytes']!=CAP or plan['max_pss_gib']!=16 or plan['poll_seconds']!=5 or plan['automatic_retry'] is not False or
            plan['code'].get(OWN)!=sha256_file(resolve(OWN))):
        raise ValueError('Followup plan scope or implementation changed')
    check_bindings(plan);return view,root,plan


def verify_stage(row):
    name=row['name'];root=exact_path(row['output'])
    if name=='resume_plan':
        from scripts.run_restored_final_resume import load_plan
        plan=load_plan(root);check_bindings(plan)
        return dict(plan_sha256=sha256_file(root/'plan.json'))
    contract=json_file(root/'contract.json');check_bindings(contract)
    if name.endswith('_prepare'):
        marker=root/('controller_prepared.json' if name=='resume_prepare' else 'prepared.json')
        if json_file(marker)['contract_sha256']!=sha256_file(root/'contract.json'):
            raise ValueError('Stage preparation contract differs')
        return dict(contract_sha256=sha256_file(root/'contract.json'),prepared_sha256=sha256_file(marker))
    report=json_file(root/'report.json')
    if json_file(root/'.done')!=dict(report_sha256=sha256_file(root/'report.json'),contract_sha256=sha256_file(root/'contract.json')):
        raise ValueError('Stage marker/report binding differs')
    if name=='native':
        from tokenizers import Tokenizer
        from scripts.evaluate_restored_native import verify_result
        from scripts.independent_final_source import verify_audit_runtime
        if (report['status']!='passed_actual_final3b_native_evaluation' or
                report['result_sha256']!=sha256_file(root/'3b_final.json') or
                report['final_training_audit_sha256']!=sha256_file(root/'final_training_audit.json')):
            raise ValueError('Native result/audit binding differs')
        main=resolve(contract['main_run'])
        if report['main_report_sha256']!=sha256_file(main/'report.json'):raise ValueError('Native main report differs')
        verify_audit_runtime(json_file(root/'final_training_audit.json'),main,contract['independent_runtime'])
        proof=json_file(main/'checkpoint_certificates/ckpt_0057000.json');result=json_file(root/'3b_final.json')
        verify_result(result,proof,Tokenizer.from_file(str(resolve(contract['tokenizer']))),contract['expected_targets'],
            json_file(resolve(contract['reference_report'])),expected_step=57000,expected_device='cuda')
        if any(report[k]!=result[k] for k in ['languages','aggregate']):raise ValueError('Native summary differs')
    elif name in ('resume','retention'):
        if name=='resume':from scripts.run_restored_final_resume import verify_completed
        else:from scripts.run_restored_retention_audit import verify_completed
        verify_completed(root,contract)
    else:
        if name=='hf':from scripts.export_restored_hf import verify_result
        elif name=='vllm':from scripts.run_restored_vllm import verify_result
        elif name=='serving':from scripts.run_restored_serving import verify_result
        else:raise ValueError('Unknown final stage')
        fresh=verify_result(root,contract)
        if any(report.get(k)!=v for k,v in fresh.items()):raise ValueError('Stage report differs from verified content')
    return dict(report_sha256=sha256_file(root/'report.json'),contract_sha256=sha256_file(root/'contract.json'))


def exited(result,row,folder):
    handle=json_file(folder/'owned/handle.json')
    if any(result[k]!=handle[k] for k in ['command','owner','process']):
        raise ValueError('Stage exit is bound to another launch handle')
    if result['command']!=row['command'] or result['exit_code']!=0 or tagged_pids(result['owner']):
        raise ValueError('Stage command/exit/owned descendants differ')
    try:live=process_record(result['process']['pid'])
    except (FileNotFoundError,ProcessLookupError):return
    if live['start_ticks']==result['process']['start_ticks']:raise ValueError('Stage controller still live')


def phase(root,row,guard,*,verify=verify_stage,poll=5):
    """An interrupted launch is preserved. A durably recorded exit may verify again."""
    folder=root/row['name'];command=row['command'];owned=None
    if folder.exists():
        if json_file(folder/'intent.json')!=row:raise ValueError('Stage intent changed')
        if not (folder/'exit.json').exists():
            raise RuntimeError('Unfinished stage; inspect recorded handles, no automatic restart')
        result=json_file(folder/'exit.json');exited(result,row,folder);guard()
        verification=verify(row)
        if (folder/'verified.json').exists() and json_file(folder/'verified.json')!=verification:
            raise ValueError('Previously verified stage changed')
        atomic_json(folder/'verified.json',verification);return verification
    guard();folder.mkdir();atomic_json(folder/'intent.json',row);(folder/'owned').mkdir()
    env=dict(os.environ,TMPDIR='/tmp',OMP_NUM_THREADS='2',NUMEXPR_NUM_THREADS='2',
        OPENBLAS_NUM_THREADS='2',MKL_NUM_THREADS='2',RAYON_NUM_THREADS='1',PYTHONUNBUFFERED='1')
    env.pop(OWNER_ENV,None)
    try:
        owned=OwnedCommand(command,folder/'owned',env)
        while owned.proc.poll() is None:
            guard();time.sleep(poll)
        handle=json_file(folder/'owned/handle.json')
        result=dict(command=command,exit_code=owned.proc.returncode,owner=owned.token,process=handle['process'])
        atomic_json(folder/'exit.json',result);exited(result,row,folder);guard()
        verification=verify(row);guard();atomic_json(folder/'verified.json',verification);return verification
    except BaseException as exc:
        atomic_json(folder/'failure.json',dict(error=repr(exc),children_preserved=True,automatic_retry=False))
        raise
    finally:
        if owned is not None:owned.close()


def run(control):
    view,root,plan=load(control)
    with (root/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        atomic_json(root/'launch_admission.json',coordinator_admission(view))
        clock=root/'started.json'
        if not clock.exists():atomic_json(clock,dict(unix=time.time()))
        started=json_file(clock)['unix'];last=[0]
        def guard():
            elapsed=time.time()-started
            if not 0<=elapsed<=plan['max_seconds']:raise RuntimeError('Followup elapsed budget exhausted')
            if time.monotonic()-last[0]<30:return
            check_bindings(plan);view.unchanged()
            from scripts.process_accounting import parse_rollup
            if parse_rollup(Path('/proc/self/smaps_rollup').read_text())['Pss']>16*2**30:
                raise RuntimeError('Followup coordinator exceeds16GiB PSS budget')
            if size_tree(root)>CAP:raise RuntimeError('Followup metadata/log budget exceeded')
            contract=json_file(view.main/'contract.json')
            future_local_capacity(contract,enforce_shared_space(view.main,contract))
            last[0]=time.monotonic()
        try:
            outputs={}
            for row in plan['stages']:
                last[0]=0;guard()
                atomic_json(root/'state.json',dict(phase='running',stage=row['name'],goal_complete=False))
                outputs[row['name']]=phase(root,row,guard)
                last[0]=0;guard()
            guard()
            report=dict(status='final_diagnostics_and_serving_verified_review_pending',stages=outputs,
                plan_sha256=sha256_file(root/'plan.json'),goal_complete=False,
                remaining=plan['remaining_after_stages'])
            atomic_json(root/'report.json',report)
            atomic_json(root/'state.json',dict(phase='complete',goal_complete=False,remaining=report['remaining']))
            return report
        except BaseException as exc:
            atomic_json(root/'state.json',dict(phase='error',error=repr(exc),children_preserved=True,goal_complete=False))
            raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--control-dir',required=True)
    mode=parser.add_mutually_exclusive_group(required=True);mode.add_argument('--prepare',action='store_true');mode.add_argument('--run',action='store_true')
    args=parser.parse_args();(prepare if args.prepare else run)(args.control_dir)
