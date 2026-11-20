"""Explicitly authorized30s boundary watcher; no LLM calls or implicit restart."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from common.data_contracts import resolve,sha256_file
from scripts.tiered_checkpoint_retention import exact_path,atomic_json,size_tree
from scripts.independent_resume_process_handoff import capture_extended_producer
from scripts.horizon_process_handoff import process_record,same_identity
from scripts.independent_boundary_handoff import execute

OWN='scripts/watch_independent_boundary.py'
WAIT=3*3600
POLL=30


def read(path):return json.loads(Path(path).read_text())


def record_event(event,root,**fields):
    with resolve('logs/pipeline_events.jsonl').open('a') as stream:
        stream.write(json.dumps(dict(utc=datetime.now(timezone.utc).isoformat(),
            stage='independent_boundary_watch',event=event,boundary_root=str(root),**fields))+'\n')
        stream.flush();os.fsync(stream.fileno())


def authorization(root,path):
    root=exact_path(resolve(root));path=exact_path(resolve(path));plan=read(root/'plan.json');approval=read(path)
    if (path!=root/'authorization.json' or approval.get('source_stop_authorized') is not True or
            approval.get('plan_sha256')!=sha256_file(root/'plan.json') or not approval.get('user_decision') or
            approval.get('watcher_code_sha256')!=sha256_file(resolve(OWN)) or
            approval.get('watch_max_seconds')!=WAIT or approval.get('watch_poll_seconds')!=POLL or
            read(root/'prepared.json')!={'plan_sha256':sha256_file(root/'plan.json')} or
            plan.get('purpose')!='own_nfs_independent_boundary_stop_v1' or plan.get('root')!=str(root) or
            type(plan.get('boundary_step')) is not int or not 10000<plan['boundary_step']<=56500 or
            plan['boundary_step']%500 or (root/'transaction').exists()):
        raise ValueError('Exact unexecuted boundary/watch authorization required')
    return root,plan,sha256_file(path)


def observation(plan):
    main=exact_path(plan['main_run']);capture=plan['source_capture'];expected=capture['processes']
    current=capture_extended_producer(main,expected['supervisor']['pid'],expected['worker']['pid'])
    if any(not same_identity(row,current['processes'][name]) for name,row in expected.items()):
        raise ValueError('Boundary source identity changed')
    observer=plan['observer_capture']
    if not same_identity(observer,process_record(observer['pid'])):raise ValueError('Boundary observer changed')
    text=(main/'training/log.jsonl').read_text()
    rows=[json.loads(line) for line in text.split('\n')[:-1] if line.strip()]
    step=max((row['step'] for row in rows),default=0)
    cert=main/'checkpoint_certificates'/f"ckpt_{plan['boundary_step']:07d}.json"
    certified=cert.exists() and read(cert).get('status')=='passed_main_checkpoint_storage' and read(cert).get('step')==plan['boundary_step']
    return dict(step=step,certified=certified)


def watch(root,path):
    root,plan,digest=authorization(root,path);folder=root/'watch_v1'
    # A previous watcher/transaction is inspected explicitly, never replaced.
    if folder.exists():raise ValueError('Preserve existing watcher evidence; no automatic relaunch')
    observation(plan)  # Identity failure produces no watcher namespace.
    folder.mkdir();started=time.monotonic()
    fixed=dict(authorization_sha256=digest,plan_sha256=sha256_file(root/'plan.json'),
        code_sha256=sha256_file(resolve(OWN)),wait_seconds=WAIT,poll_seconds=POLL)
    atomic_json(folder/'intent.json',fixed);(folder/'pid').write_text(str(os.getpid())+'\n')
    record_event('authorized_watch_started',root,boundary_step=plan['boundary_step'],poll_seconds=POLL)
    with (folder/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            while True:
                if time.monotonic()-started>WAIT:raise TimeoutError('Boundary wait expired; main unchanged')
                if sha256_file(resolve(path))!=digest:raise ValueError('Boundary watcher authorization changed')
                if (sha256_file(root/'plan.json')!=fixed['plan_sha256'] or sha256_file(resolve(OWN))!=fixed['code_sha256']):
                    raise ValueError('Boundary watcher plan/code changed')
                if size_tree(root)>48*2**20:raise RuntimeError('Boundary metadata budget exceeded')
                current=observation(plan)
                if current['step']>plan['boundary_step']:raise RuntimeError('Boundary already passed; main unchanged')
                atomic_json(folder/'state.json',dict(phase='waiting',**current))
                if current['step']==plan['boundary_step'] and current['certified']:
                    result=execute(root,path)
                    atomic_json(folder/'report.json',dict(status='authorized_boundary_stopped',result=result,training_restarted=False))
                    atomic_json(folder/'state.json',dict(phase='complete',training_restarted=False))
                    record_event('authorized_boundary_stopped',root,boundary_step=plan['boundary_step'],training_restarted=False)
                    return result
                time.sleep(POLL)
        except BaseException as exc:
            atomic_json(folder/'state.json',dict(phase='error',error=repr(exc),automatic_restart=False))
            record_event('boundary_watch_error',root,error=repr(exc),automatic_restart=False)
            raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--boundary-root',required=True);p.add_argument('--authorization',required=True)
    a=p.parse_args();watch(a.boundary_root,a.authorization)
