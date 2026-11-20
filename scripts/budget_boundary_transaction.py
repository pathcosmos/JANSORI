"""Exact12-process stopped-boundary transaction, no automatic invocation.

The caller must bind authorization, code, namespaces, capacity, full parent copy
and callbacks in a reviewed immutable plan. A failed transaction preserves state
for diagnosis; it never adds SIGKILL or starts another producer.
"""
from contextlib import ExitStack
import signal
import time

from scripts.horizon_process_handoff import BoundProcessSet
from scripts.horizon_exit_wait import terminate_stopped, wait_exit_only
from scripts.tiered_checkpoint_retention import exact_path, atomic_json

ROLES={'supervisor','worker',*[f'rank{i}' for i in range(8)]}
CONTROLLERS={'observer','followup'}


def stop_transaction(capture, controllers, output, *, precheck, retention_lock,
                     protect_saved, after_stop):
    output=exact_path(output)
    if output.exists():raise ValueError('Preserve existing 50k boundary transaction for explicit recovery')
    if set(capture['processes'])!=ROLES or set(controllers)!=CONTROLLERS:
        raise ValueError('Exact10 producer plus observer/followup roles required')
    records=dict(capture['processes'],**controllers)
    if len({r['pid'] for r in records.values()})!=12:raise ValueError('Duplicate boundary PID identity')
    precheck();output.mkdir();phase='binding'
    def state(name,**extra):
        nonlocal phase
        phase=name;atomic_json(output/'state.json',dict(phase=name,**extra))
    try:
        with BoundProcessSet(records,output/'signals.jsonl') as bound:
            workers=['worker',*[f'rank{i}' for i in range(8)],'observer','followup']
            state('pausing_workers_and_waiting_controllers')
            bound.signal(workers,signal.SIGSTOP);bound.wait(workers,stopped=True)
            with ExitStack() as held:
                deadline=time.monotonic()+120
                while True:
                    bound.signal(['supervisor'],signal.SIGSTOP);bound.wait(['supervisor'],stopped=True)
                    try:held.enter_context(retention_lock());break
                    except BlockingIOError:
                        bound.signal(['supervisor'],signal.SIGCONT)
                        if time.monotonic()>deadline:raise TimeoutError('50k retention lock did not settle')
                        time.sleep(.1)
                state('all12_paused');precheck();receipt=protect_saved()
                if receipt.get('status')!='verified_protected_parent':
                    raise ValueError('Full protected parent SHA receipt required before termination')
                if any(s not in ('T','t') for s in bound.states().values()):
                    raise ValueError('All captured processes must remain paused before termination')
                atomic_json(output/'protected_boundary.json',receipt)
                state('verified_protected_parent_before_termination')
                for name in ['supervisor','observer','followup',*[f'rank{i}' for i in range(8)],'worker']:
                    terminate_stopped(bound,name,grace_seconds=30)
                wait_exit_only(bound,records,timeout=30);state('exact12_exited')
            result=after_stop();state('boundary_sealed_ready_for_diagnostic')
            atomic_json(output/'report.json',dict(status='stopped_verified_50k_boundary',boundary=receipt,
                result=result,training_restarted=False,checkpoints_deleted=False,goal_complete=False))
            return result
    except BaseException as exc:
        atomic_json(output/'failure.json',dict(phase=phase,error=repr(exc),capture=capture,controllers=controllers,
            outputs_preserved=True,automatic_resume=False,automatic_restart=False,
            recovery='Inspect pidfd journal and exact PID/start ticks; do not restart on an observation timeout.'))
        raise
