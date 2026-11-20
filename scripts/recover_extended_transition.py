"""Explicit, single-use recovery of the certified10000 boundary exit race.

Frozen training/transition modules are unchanged. Exits are observed through
pidfds, including the already dead rank0; dying cwd/cmdline is never polled.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.paths import PROJECT_ROOT
from common.data_contracts import resolve, identity, sha256_file
from scripts.horizon_process_handoff import process_record, same_identity, children
from scripts.seal_horizon_handoff import unchanged, write_once, json_bytes, require_parent_absent
from scripts.tiered_checkpoint_retention import atomic_json, inventory, size_tree
from scripts.extended_boundary_handoff import validate_plan, validate_controllers
from scripts.extended_horizon_storage import enforce_shared_space, GIB
from train.final_resume_probe import file_stats
from train.extended_handoff import require_waiting_controllers_absent

SCOPE = 'recover_verified10000_boundary_and_resume_cumulative57000'
BASE = 'runs/3b_57k_transition_v2'


class PinnedRecovery:
    """Pin all expected identities before any signal; never adopt replacements."""
    def __init__(self, records, journal):
        self.records, self.fds, self.absent = records, {}, set()
        self.journal = Path(journal)
        if not records or len({r['pid'] for r in records.values()}) != len(records):
            raise ValueError('Require distinct captured identities')
        try:
            for name, row in records.items():
                try:
                    fd = os.pidfd_open(row['pid'])
                except ProcessLookupError:
                    self.absent.add(name)
                    continue
                self.fds[name] = fd
                try:
                    stat = (Path('/proc')/str(row['pid'])/'stat').read_text().rsplit(')', 1)[1].split()
                except FileNotFoundError:
                    if self.exited(name):continue
                    raise
                if int(stat[19]) != row['start_ticks']:
                    raise ValueError('PID reused: '+name)
                if not self.exited(name):self.require_stopped(name)
            self.check_topology()
        except BaseException:
            self.close();raise

    def exited(self, name):
        return name in self.absent or bool(select.select([self.fds[name]], [], [], 0)[0])

    def require_stopped(self, name):
        now = process_record(self.records[name]['pid'])
        if not same_identity(self.records[name], now) or now['state'] not in ('T', 't'):
            raise ValueError('Captured process is not unchanged/stopped: '+name)

    def check_topology(self):
        allowed = {r['pid'] for r in self.records.values()}
        for name, row in self.records.items():
            if not self.exited(name):
                self.require_stopped(name)
                if children(row['pid']) - allowed:
                    raise ValueError('Unexpected descendant: '+name)

    def event(self, event, **fields):
        with self.journal.open('a') as f:
            f.write(json.dumps(dict(time_ns=time.time_ns(), event=event, **fields))+'\n')
            f.flush();os.fsync(f.fileno())

    def terminate(self, name, *, timeout=30):
        if not 0 < timeout <= 60:raise ValueError('Invalid exit deadline')
        if self.exited(name):
            self.event('already_exited_no_signal', name=name);return
        self.check_topology()
        self.event('terminate_intent', name=name, identity=self.records[name])
        # After TERM, identity is the pinned kernel handle. Inspecting cwd or
        # cmdline here would recreate the failure being recovered.
        for sig in (signal.SIGTERM, signal.SIGCONT):
            if self.exited(name):break
            try:signal.pidfd_send_signal(self.fds[name], sig)
            except ProcessLookupError:
                if not self.exited(name):raise
            self.event('signal_sent', name=name, signal=int(sig))
        if not select.select([self.fds[name]], [], [], timeout)[0]:
            raise TimeoutError('Pinned exit deadline; no fallback kill/retry: '+name)
        self.event('pinned_exit_observed', name=name)

    def close(self):
        for fd in self.fds.values():os.close(fd)
        self.fds.clear()

    def __enter__(self):return self
    def __exit__(self, *args):self.close()


def context():
    original = resolve(BASE+'/plan.json');plan = json.loads(original.read_text());paths = validate_plan(plan)
    root, source = paths['destination'], paths['source']
    failure = json.loads(resolve(BASE+'/failure.json').read_text())
    boundary = json.loads(resolve(BASE+'/boundary10000/state.json').read_text())
    if failure['phase'] != 'protect_stop_seal' or 'FileNotFoundError' not in failure['error'] or boundary['phase'] != 'protected_parent_verified':
        raise ValueError('Different failure needs separate inspection')
    draft = json.loads(paths['draft'].read_text());unchanged({**draft['code'], **draft['evidence']})
    capture = json.loads(paths['capture'].read_text());controllers = json.loads(paths['controllers'].read_text())
    owner = json.loads((root/'.preparation_owner.json').read_text())
    proof_path = source/'checkpoint_certificates/ckpt_0010000.json';proof = json.loads(proof_path.read_text())
    cp = source/'training/ckpt_0010000';parent = root/'parent_checkpoint/ckpt_0010000'
    receipt_path = parent.with_name(parent.name+'.receipt.json');receipt = json.loads(receipt_path.read_text())
    expected = dict(version=1,owner_id=owner['owner_id'],source=str(cp),destination=str(parent),
                    proof_id=identity(proof),step=10000,bytes=proof['bytes'])
    if (receipt != boundary['protected'] or receipt['status'] != 'verified_protected_parent'
            or receipt['transaction'] != expected or receipt['proof_id'] != identity(proof)
            or owner['owner_id'] != draft['tiered_retention']['run_id']):
        raise ValueError('Protected parent authorization/receipt differs')
    inventory(cp,proof);inventory(parent,proof)
    if receipt['stats'] != file_stats(parent):raise ValueError('Protected bytes changed since SHA certification')
    return dict(plan=plan,paths=paths,main=root,source=source,draft=draft,capture=capture,
                controllers=controllers,owner=owner,proof=proof,parent=parent,cp=cp,
                receipt=receipt,proof_path=proof_path,receipt_path=receipt_path)


def execute(plan_path):
    plan_path=resolve(plan_path);p=json.loads(plan_path.read_text())
    if (p.get('scope')!=SCOPE or p.get('authorized') is not True or p.get('target_steps')!=57000
            or p.get('parent_step')!=10000 or p.get('user_decision')!='복구 후 57,000스텝으로 진행 (권장)'
            or p.get('output')!=BASE+'/recovery_execution_v1' or p.get('automatic_retry') is not False):
        raise ValueError('Require explicit exact recovery authorization')
    unchanged({**p['code'],**p['inputs']})
    ctx=context();main=ctx['main'];draft=ctx['draft'];output=resolve(p['output'])
    if output.exists():raise ValueError('Recovery output exists; no automatic retry')
    if any((main/n).exists() for n in ('parent_handoff.json','lineage','horizon_probe','contract.json')):
        raise ValueError('Later-stage evidence exists; no implicit adoption')
    enforce_shared_space(main,draft)
    phase='precheck';began=time.monotonic()
    def guard():
        unchanged(p['code'])
        if time.monotonic()-began>600 or size_tree(resolve(BASE))>GIB:
            raise RuntimeError('Boundary recovery budget exceeded')
        pss=sum(int(s.split()[1])*1024 for s in Path('/proc/self/smaps_rollup').read_text().splitlines() if s.startswith('Pss:'))
        if pss>16*GIB:raise RuntimeError('Boundary recovery PSS exceeded')
        enforce_shared_space(main,draft)
    def event(case,**kw):
        atomic_json(output/'state.json',dict(phase='running',case=case,**kw))
        with resolve('logs/pipeline_events.jsonl').open('a') as f:
            f.write(json.dumps(dict(utc=datetime.now(timezone.utc).isoformat(),stage='57k_recovery',event=case,**kw))+'\n')
    with (resolve(BASE)/'controller.lock').open('r') as controller_lock:
        fcntl.flock(controller_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        output.mkdir();(output/'pid').write_text(str(os.getpid())+'\n')
        write_once(output/'execution_binding.json',json_bytes(dict(plan_sha256=sha256_file(plan_path),plan=p)))
        try:
            records=dict(ctx['capture']['processes']);records.update(ctx['controllers'])
            with PinnedRecovery(records,output/'signals.jsonl') as bound:
                if not bound.exited('supervisor') or not bound.exited('rank0'):
                    raise ValueError('Inspected failure state changed')
                validate_controllers(ctx['controllers']);guard();context()
                phase='protected_full_sha_recheck';event(phase)
                inventory(ctx['cp'],ctx['proof'],hashes=True,workers=8)
                inventory(ctx['parent'],ctx['proof'],hashes=True,workers=8)
                context();guard()
                original=resolve(json.loads((ctx['source']/'contract.json').read_text())['legacy_retention']['source_run'])
                with ExitStack() as stack:
                    for root in sorted((ctx['source'],original)):
                        lock=stack.enter_context((root/'retention.lock').open('r'))
                        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    for name in [*[f'rank{i}' for i in range(8)],'worker',*sorted(ctx['controllers'])]:
                        phase='terminate_'+name;event(phase);guard();bound.terminate(name)
            phase='seal10000';event(phase);guard()
            deadline=time.monotonic()+30
            while True:
                try:
                    require_parent_absent(ctx['capture'],ctx['source'])
                    require_waiting_controllers_absent(ctx['controllers']);break
                except ValueError:
                    if time.monotonic()>deadline:raise
                    time.sleep(.1)
            from scripts.seal_extended_handoff import seal_extension
            seal_extension(ctx['source'],main,ctx['capture'],ctx['controllers'],owner_id=ctx['owner']['owner_id'],step=10000)
            phase='snapshot_maintenance';event(phase);guard()
            from scripts.extended_horizon_retention import ExtendedRetention
            retention=ExtendedRetention(main,draft,authorized=True).maintain()
            if retention['uncertified_nfs'] or len(retention['nfs_checkpoints'])!=3 or len(retention['local_checkpoints'])>15:
                raise ValueError('Global retention did not settle')
            write_once(output/'boundary_retention.json',json_bytes(retention));guard()
            phase='prepare_actual8gpu';event(phase)
            from scripts.prepare_extended_probe import prepare
            prepare(main,ctx['paths']['draft'],ctx['paths']['capture'])
            phase='actual8gpu_validation';event(phase)
            from scripts.run_extended_probe import execute as probe
            probe(main/'horizon_probe/plan.json')
            phase='activate57k';event(phase);unchanged(p['code'])
            from scripts.activate_extended_training import activation
            activation(main,ctx['paths']['draft'],ctx['paths']['capture'],ctx['paths']['authorization'])
            enforce_shared_space(main,draft);phase='start57k';event(phase)
            command=[sys.executable,'-u','scripts/supervise_extended_training.py','--run-dir',str(main),'--run']
            env=dict(os.environ,OMP_NUM_THREADS='2',MKL_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',
                     PYTHONUNBUFFERED='1',TMPDIR=draft['tiered_retention']['temp_root'])
            with (output/'supervisor.log').open('x') as log:
                proc=subprocess.Popen(command,cwd=PROJECT_ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            result=dict(status='57k_supervisor_launched_verification_pending',supervisor=process_record(proc.pid),
                        parent_step=10000,target_step=57000,first_new_committed_update_verified=False,goal_complete=False)
            write_once(output/'report.json',json_bytes(result));event('57k_supervisor_launched',pid=proc.pid)
            atomic_json(output/'state.json',dict(phase='complete',case='main_launched_first_update_pending'))
            return result
        except BaseException as exc:
            atomic_json(output/'failure.json',dict(phase=phase,error=repr(exc),automatic_retry=False,outputs_preserved=True))
            event('recovery_exception',phase_at_failure=phase,error=repr(exc))
            atomic_json(output/'state.json',dict(phase='error',case=phase,error=repr(exc)));raise


if __name__=='__main__':
    def interrupted(signum,frame):raise InterruptedError('Recovery interrupted: '+str(signum))
    signal.signal(signal.SIGTERM,interrupted)
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--plan',required=True)
    parser.add_argument('--execute',action='store_true',required=True);args=parser.parse_args();execute(args.plan)
