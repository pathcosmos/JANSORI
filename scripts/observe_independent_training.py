"""Thirty-second own-NFS runtime observer; never restarts, signals or prunes.

Producer certificates and completion markers are observations, not refreshed
payload hashes, final committed-ledger audits or whole-Goal success.
"""
import argparse
from datetime import datetime,timezone
import fcntl
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from common.data_contracts import resolve,sha256_file,identity,artifact_path
from scripts.tiered_checkpoint_retention import exact_path,atomic_json,size_tree,records
from scripts.independent_runtime_view import RuntimeView,json_file
from scripts.observe_horizon_training import rows_from_trace,summarize,progress_state

OWN='scripts/observe_independent_training.py'


def admit_output(view,output):
    output=exact_path(resolve(output));pool=view.contract['shared_storage']['nfs_pools']['observer']
    if (pool['budget_bytes']!=2**30 or len(pool['paths'])!=1 or
            output!=exact_path(pool['paths'][0])/'independent_v1' or
            view.binding.get('observer_output')!=str(output) or
            output.is_relative_to(view.main) or view.main.is_relative_to(output) or
            output.is_relative_to(view.control) or view.control.is_relative_to(output) or
            exact_path(pool['paths'][0]).stat().st_dev!=view.main.stat().st_dev or
            view.binding['code'].get(OWN)!=sha256_file(resolve(OWN))):
        raise ValueError('Exact independently bound observer namespace/pool/code required')
    return dict(purpose='independent_main_observer_v1',source_run=str(view.main),runtime_contract=str(view.path),
        runtime_contract_sha256=view.digest,output=str(output),interval_seconds=30,resource_interval_seconds=600,
        stall_seconds=1200,max_rss_bytes=2*2**30,max_seconds=22*86400,pool=pool,
        scope='Producer receipt observation only; no payload SHA refresh, launch/restart/signals/deletion/LLM/push/Goal completion.')


def cycle(view,history,now):
    observation=view.observe();summary=summarize(rows_from_trace(view.main/'training/log.jsonl'),view.config)
    old=dict(history);history=dict(history);history['certificate_hashes']=dict(history.get('certificate_hashes',{}))
    processes=observation['processes'];worker=(processes or {}).get('launcher');supervisor=(processes or {}).get('supervisor')
    if observation['phase']=='await_independent_execution':
        kind='await_independent_execution'
    else:
        kind,history=progress_state(summary,history,now,supervisor_live=supervisor is not None,
            worker_live=worker is not None and (observation['consumption'] is None or processes['all8_consumed_live']),
            phase=observation['phase'],worker_pid=worker['pid'] if worker else 0,stall_seconds=1200)
        if kind=='running' and observation['consumption'] is None:kind='restoring_await_all8_consumption'
    if observation['completion'] is not None:
        if summary['logged_step']!=57000:raise ValueError('Target completion and observed final training step differ')
        kind='target_saved_final_review_pending'
    events=[]
    def event(key,name,**values):events.append(dict(event_id=key,event=name,**values))
    if old.get('source_state')!=kind:
        event('state:'+identity([kind,now]),'training_observation_state_changed',previous=old.get('source_state'),state=kind,step=summary['logged_step'])
    history['source_state']=kind
    for path in sorted((view.main/'checkpoint_certificates').glob('*.json')):
        digest=sha256_file(path);previous=history['certificate_hashes'].get(path.name)
        if previous and previous!=digest:raise ValueError('Published checkpoint certificate changed')
        if not previous:
            proof=json_file(path);records(proof)
            if (proof['status']!='passed_main_checkpoint_storage' or proof['step']!=int(path.stem[5:]) or
                    proof['training_contract_id']!=view.acceptance['parent_training_contract_id'] or
                    resolve(proof['checkpoint'])!=view.main/'training'/path.stem):
                raise ValueError('Observed certificate main/step/contract differs')
            history['certificate_hashes'][path.name]=digest
            event('certificate:'+path.name,'checkpoint_certified',step=proof['step'],bytes=proof['bytes'],
                certificate=artifact_path(path),scope='Producer receipt; no fresh payload SHA or residency claim.')
    if summary['logged_step']:
        milestone=(summary['logged_step']//500)*500
        event('milestone:'+str(milestone),'training_milestone_observed',step=milestone,observed_log_step=summary['logged_step'])
    for step,value in summary['evaluations'].items():event('evaluation:'+step,'source_evaluation_observed',**value)
    if observation['completion'] is not None:
        event('target_saved','main_target_report_observed_final_review_pending',step=57000,goal_complete=False)
    out=dict(phase='complete' if observation['completion'] is not None else 'running',case=kind,
        done=summary['logged_step'],total=57000,observation=observation,summary=summary,
        last_retention_report=json_file(view.main/'retention_state.json') if (view.main/'retention_state.json').exists() else None,
        scope='Read-only receipts/log observation, not actual full-state/committed-ledger/final quality verification.')
    return out,history,events


def record_events(output,source,history,events,utc):
    seen=set(history.get('seen',[]))
    for value in events:
        key=value['event_id']
        if key in seen:continue
        row=dict(utc=utc,stage='independent_main_observer',source_run=artifact_path(source),**value)
        for path in [output/'events.jsonl',resolve('logs/pipeline_events.jsonl')]:
            with path.open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
        seen.add(key)
    history['seen']=sorted(seen)


def observe(control,output):
    view=RuntimeView(control);output=exact_path(resolve(output));binding=admit_output(view,output)
    initial_history=dict(seen=[],certificate_hashes={})
    if 'observer_handoff' in view.binding:
        from scripts.independent_observer_handoff import seed_history
        initial_history=seed_history(view,output)
    output.mkdir(parents=True,exist_ok=True)
    with (output/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        cp=output/'contract.json'
        if cp.exists() and json_file(cp)!=binding:raise ValueError('Observer output already bound to another runtime')
        atomic_json(cp,binding);(output/'pid').write_text(str(os.getpid())+'\n')
        cursor=output/'cursor.json'
        if cursor.exists():history=json_file(cursor)
        else:
            history=initial_history
            atomic_json(cursor,history)
        start=time.monotonic()
        while True:
            tick=time.monotonic();utc=datetime.now(timezone.utc).isoformat()
            try:
                rss=int(next(r.split()[1] for r in Path('/proc/self/status').read_text().splitlines() if r.startswith('VmRSS:')))*1024
                if (rss>binding['max_rss_bytes'] or tick-start>binding['max_seconds'] or
                        size_tree(Path(binding['pool']['paths'][0]))+8*2**20>binding['pool']['budget_bytes']):
                    raise RuntimeError('Independent observer resource budget')
                state,next_history,events=cycle(view,history,time.time())
                if time.time()-history.get('last_resource_unix',0)>=binding['resource_interval_seconds']:
                    from scripts.report_resource_usage import snapshot
                    begun=time.time();resources=snapshot();atomic_json(output/'resources.json',resources)
                    events.append(dict(event_id='resources:'+resources['utc'],event='resource_sample',resources=resources))
                    next_history['last_resource_unix']=begun
                record_events(output,view.main,next_history,events,utc)
                atomic_json(output/'state.json',state);atomic_json(cursor,next_history);history=next_history
                if state['phase']=='complete':
                    report=dict(status='observation_complete_final_reviews_pending',observation=state,goal_complete=False)
                    atomic_json(output/'report.json',report)
                    atomic_json(output/'.done',dict(report_sha256=sha256_file(output/'report.json'),contract_sha256=sha256_file(cp)))
                    return report
            except Exception as exc:
                record_events(output,view.main,history,[dict(event_id='error:'+identity(repr(exc)),event='observer_check_error',reason=repr(exc))],utc)
                atomic_json(output/'state.json',dict(phase='running',case='observation_error',reason=repr(exc)))
                atomic_json(cursor,history)
                if isinstance(exc,RuntimeError) and str(exc)=='Independent observer resource budget':raise
            time.sleep(max(0,binding['interval_seconds']-(time.monotonic()-tick)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--control-dir',required=True);parser.add_argument('--run-dir',required=True)
    args=parser.parse_args();os.environ['CUDA_VISIBLE_DEVICES']='';observe(args.control_dir,args.run_dir)
