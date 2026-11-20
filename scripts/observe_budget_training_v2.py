"""Read-only50k observation under exact contract/output/process identities.

Never restarts, signals, copies or deletes; milestones observe receipts only.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve,artifact_path,atomic_json,sha256_file,identity
from scripts.observe_horizon_training import rows_from_trace,summarize,progress_state
from scripts.horizon_process_handoff import process_record
from scripts.tiered_checkpoint_retention import size_tree


def admit_source(source,output,contract,config):
    from train.budget_horizon_handoff import validate_configuration,load_handoff
    source,output=resolve(source),resolve(output)
    if (contract.get('purpose')!='main_training' or not contract.get('main_training_authorized') or
            config.get('max_steps')!=50000 or resolve(contract['main_run'])!=source or
            output==source or output!=resolve('runs/3b_50k_transition_v1/checkpoint_observer_v2')):
        raise ValueError('Expected authorized50k main and independent observer')
    pools=contract['shared_storage']['nfs_pools']
    matches=[pool for pool in pools.values() if pool['paths']==[str(output.parent)]]
    if len(matches)!=1 or matches[0]['budget_bytes']!=2**30 or size_tree(output.parent)+32*2**20>2**30:
        raise ValueError('Observer lacks its exact reserved1GiB metadata pool')
    validate_configuration(config,contract)
    load_handoff(source,contract,config)  # Actual8GPU/ancestry/protection admission, read-only.


def process_identity(pid,role,source):
    if type(pid) is not int or pid<=1:return None
    if role not in ('supervisor','worker'):raise ValueError('Unknown producer role')
    try:row=process_record(pid)
    except (FileNotFoundError,ProcessLookupError,ValueError):return None
    args=row['argv'];source=resolve(source)
    if row['cwd']!=str(resolve('.')) or args.count('--run-dir')!=1:return None
    pos=args.index('--run-dir')
    if pos+1==len(args) or resolve(args[pos+1])!=source:return None
    if role=='supervisor':
        if not any(resolve(arg)==resolve('scripts/supervise_budget_training.py') for arg in args):return None
    else:
        if ('torch.distributed.run' not in args or args.count('--module')!=1 or
                args.index('--module')+1==len(args) or args[args.index('--module')+1]!='train.monitored_budget_horizon'):
            return None
    return {key:row[key] for key in ('pid','start_ticks','argv_sha256','state')}


def observe(source, output):
    source,output=resolve(source),resolve(output)
    output.relative_to(resolve('runs'))
    if output.is_relative_to(source):
        raise ValueError('Observer output must be independent of managed training')
    c=json.loads((source/'contract.json').read_text());cfg=json.loads((source/'main.json').read_text())
    admit_source(source,output,c,cfg)
    output.mkdir(parents=True,exist_ok=True)
    binding=dict(source_run=artifact_path(source),source_contract_sha256=sha256_file(source/'contract.json'),
        source_config_sha256=sha256_file(source/'main.json'),interval=30,stall_seconds=1200,resource_interval=600,
        max_rss_mib=2048,max_seconds=22*86400,code={p:sha256_file(resolve(p)) for p in
             ['scripts/observe_budget_training_v2.py','scripts/observe_horizon_training.py','scripts/horizon_process_handoff.py','scripts/audit_main_training.py','scripts/report_resource_usage.py']},
        scope='Read-only progress and producer receipt observation; no training/restart/deletion/upload/LLM/push; no Goal completion claim.')
    with (output/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        cp=output/'contract.json'
        if cp.exists() and json.loads(cp.read_text())!=binding:
            raise ValueError('Observer binding changed; preserve and use a new observer run')
        atomic_json(cp,binding);(output/'pid').write_text(str(os.getpid())+'\n')
        cursor=output/'cursor.json'
        history=json.loads(cursor.read_text()) if cursor.exists() else dict(seen=[],certificate_hashes={})
        start=time.monotonic()
        def event(key,kind,**kwargs):
            if key in history['seen']:
                return
            record=dict(utc=datetime.now(timezone.utc).isoformat(),stage='main_training_observer',
                        event=kind,event_id=key,source_run=artifact_path(source),**kwargs)
            for path in [output/'events.jsonl',resolve('logs/pipeline_events.jsonl')]:
                with path.open('a') as stream:
                    stream.write(json.dumps(record,ensure_ascii=False)+'\n')
            history['seen'].append(key)
        while True:
            poll_start=time.monotonic()
            try:
                rss=int(next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('VmRSS:')))*1024
                if rss>binding['max_rss_mib']*2**20 or time.monotonic()-start>binding['max_seconds'] or size_tree(output)>32*2**20:
                    raise RuntimeError('Observer resource budget')
                if sha256_file(source/'contract.json')!=binding['source_contract_sha256'] or sha256_file(source/'main.json')!=binding['source_config_sha256']:
                    raise ValueError('Live main contract/configuration changed')
                state=json.loads((source/'state.json').read_text())
                pid=int((source/'pid').read_text());worker=state.get('worker_pid',0)
                summary=summarize(rows_from_trace(resolve(cfg['out_dir'])/'log.jsonl'),cfg)
                supervisor=process_identity(pid,'supervisor',source);launcher=process_identity(worker,'worker',source)
                kind,history=progress_state(summary,history,time.time(),supervisor_live=supervisor is not None,
                    worker_live=launcher is not None,phase=state.get('phase'),worker_pid=worker,
                    stall_seconds=binding['stall_seconds'])
                for path in sorted((source/'checkpoint_certificates').glob('*.json')):
                    digest=sha256_file(path)
                    prior=history['certificate_hashes'].get(path.name)
                    if prior and prior!=digest:
                        raise ValueError('Published producer certificate changed')
                    if not prior:
                        proof=json.loads(path.read_text())
                        if proof['status']!='passed_main_checkpoint_storage' or proof['step']!=int(path.stem.split('_')[1]):
                            raise ValueError('Unexpected producer certificate')
                        history['certificate_hashes'][path.name]=digest
                        event('certificate:'+path.name,'checkpoint_certified',step=proof['step'],bytes=proof['bytes'],
                              certificate=artifact_path(path),scope='Producer receipt; not a fresh payload rehash or current residency claim.')
                for step,evaluation in summary['evaluations'].items():
                    event('evaluation:'+step,'source_evaluation_observed',**evaluation)
                if history.get('source_state')!=kind:
                    event('state:'+str(time.time_ns()),'training_observation_state_changed',previous=history.get('source_state'),state=kind,step=summary['logged_step'])
                history['source_state']=kind
                if summary['logged_step']:
                    event('milestone:'+str(summary['logged_step']//500),'training_milestone_observed',step=summary['logged_step'])
                if time.time()-history.get('last_resource_unix',0)>=binding['resource_interval']:
                    from scripts.report_resource_usage import snapshot
                    resource_sample_started=time.time()
                    resources=snapshot();atomic_json(output/'resources.json',resources)
                    event('resources:'+resources['utc'],'resource_sample',snapshot=artifact_path(output/'resources.json'),resources=resources)
                    history['last_resource_unix']=resource_sample_started
                retention=json.loads((source/'retention_state.json').read_text()) if (source/'retention_state.json').exists() else None
                out=dict(phase='running',case=kind,done=summary['logged_step'],total=cfg['max_steps'],
                         supervisor_pid=pid,worker_pid=worker,process_identities={'supervisor':supervisor,'worker':launcher},last_progress_unix=history['last_progress_unix'],
                         certificates_ever_observed=len(history['certificate_hashes']),summary=summary,
                         last_retention_report=retention,retention_scope='Last manager report, not an independent current storage audit.')
                atomic_json(output/'state.json',out);atomic_json(cursor,history)
                if (source/'.done').exists():
                    marker=json.loads((source/'.done').read_text())
                    if (marker.get('report_sha256')!=sha256_file(source/'report.json') or
                            marker.get('contract_sha256')!=binding['source_contract_sha256']):
                        raise ValueError('Source completion proof changed')
                    report=dict(status='observation_complete',source_report_sha256=marker['report_sha256'],observation=out,scope=binding['scope'])
                    atomic_json(output/'report.json',report)
                    atomic_json(output/'.done',dict(report_sha256=sha256_file(output/'report.json'),contract_sha256=sha256_file(cp)))
                    atomic_json(output/'state.json',dict(phase='complete',done=summary['logged_step'],total=cfg['max_steps']))
                    event('observation_complete','main_target_report_observed_final_review_pending',step=summary['logged_step'])
                    atomic_json(cursor,history)
                    break
            except Exception as exc:
                detail=type(exc).__name__+': '+str(exc)
                event('error:'+identity(detail),'observer_check_error',reason=detail)
                atomic_json(output/'state.json',dict(phase='running',case='observation_error',reason=detail))
                atomic_json(cursor,history)
                if isinstance(exc,RuntimeError) and str(exc)=='Observer resource budget':
                    raise
            time.sleep(max(0,binding['interval']-(time.monotonic()-poll_start)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-run',required=True)
    p.add_argument('--run-dir',required=True)
    a=p.parse_args();observe(a.source_run,a.run_dir)
