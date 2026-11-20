"""Versioned supervisor for an explicitly bound, independently tested own-NFS resume.

Never stops/adopts an existing producer. Requires actual sealed8GPU acceptance,
original controller/GPU locks and a fresh all-rank consumption gate before any
retention mutation. The original57k numerical worker/config/IDs stay unchanged.
Preparation only until operational observer/final consumers are also migrated.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from common.data_contracts import resolve,sha256_file,artifact_path
from scripts.tiered_checkpoint_retention import exact_path,atomic_json,inventory,size_tree
from scripts.horizon_probe_process import OwnedCommand,OWNER_ENV,tagged_pids
from scripts.horizon_process_handoff import process_record
from scripts.seal_horizon_handoff import require_parent_absent
from scripts.extended_horizon_storage import enforce_shared_space
from scripts.extended_horizon_retention import ExtendedRetention
from scripts.supervise_extended_training import seal
from scripts.prepare_latest_independent_revision import prepare,checkpoint_names,select_latest
from train.independent_runtime_contract import validate_contract,validate_attempt,verify_consumption_rows,MODULE
from train.independent_operational_admission import PreparedOperationalAdmission

GIB=2**30


def self_record():
    """Capture self without weakening process_record's no-self signal guard."""
    p=Path('/proc/self');s=(p/'stat').read_text().rsplit(')',1)[1].split();raw=(p/'cmdline').read_bytes()
    return dict(pid=os.getpid(),start_ticks=int(s[19]),state=s[0],ppid=int(s[1]),
        pgid=int(s[2]),sid=int(s[3]),argv_sha256=hashlib.sha256(raw).hexdigest(),
        argv=[v.decode() for v in raw.split(b'\0') if v],cwd=str(p.joinpath('cwd').resolve()),uid=p.stat().st_uid)


@contextmanager
def exclusive_controls(main,control):
    # Same original lock: the live legacy controller cannot race this entry.
    with (main/'controller.lock').open('a') as first,(control/'controller.lock').open('a') as second:
        fcntl.flock(first,fcntl.LOCK_EX|fcntl.LOCK_NB)
        fcntl.flock(second,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


def require_absent(main,control,binding):
    require_parent_absent(binding['source_capture'],main)
    for handle in control.glob('attempts/*/launch/handle.json'):
        row=json.loads(handle.read_text())
        if tagged_pids(row['owner']):raise ValueError('Previous owned attempt remains live; never adopt/restart automatically')
    # Also cover launch-before-handle crashes and other independent controllers.
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            args=[s.decode() for s in (proc/'cmdline').read_bytes().split(b'\0') if s]
            if MODULE not in args or '--execution' not in args:continue
            p=(proc/'cwd').resolve()/args[args.index('--execution')+1]
            if resolve(json.loads(p.read_text())['main_run'])==main:
                raise ValueError('An independent main worker already owns this run')
        except (FileNotFoundError,ProcessLookupError):continue


def runtime_inputs(control):
    control=exact_path(resolve(control));path=control/'runtime_contract.json'
    runtime,contract,accepted=validate_contract(path);binding=runtime['binding'];main=resolve(binding['main_run'])
    from train.extended_handoff import validate_configuration
    config=json.loads((main/'main.json').read_text());validate_configuration(config,contract)
    if (contract['configurations']!={'main.json':sha256_file(main/'main.json')} or
            resolve(config['out_dir'])!=main/'training' or config['keep_ckpts']!=57001):
        raise ValueError('Original main numerical configuration/pruning differs')
    template=exact_path(resolve(binding['revision_template']['path']))
    if (not template.is_relative_to(Path(contract['shared_storage']['nfs_pools']['transition']['paths'][0])) or
            sha256_file(template)!=binding['revision_template']['sha256']):
        raise ValueError('Exact pinned NFS revision template required')
    if type(binding['elapsed_before_seconds']) not in (int,float) or not math.isfinite(binding['elapsed_before_seconds']) or not 0<=binding['elapsed_before_seconds']<contract['max_seconds']:
        raise ValueError('Reviewed elapsed budget required')
    if os.environ.get('TMPDIR')!='/tmp':raise ValueError('Supervisor TMPDIR must be /tmp, not the worker scratch')
    return runtime,contract,accepted,main


def execution_settings(contract,main,hardware):
    expected=exact_path(contract['tiered_retention']['temp_root'])
    scratch=exact_path(Path(hardware['storage']['scratch_dir'])/main.name)
    if scratch!=expected:raise ValueError('Nested/different worker temporary path')
    if (len(hardware['gpus'])!=8 or hardware['gpus']!=contract['gpu_identity'] or
            hardware['memory']['cache_aware_admission']['headroom_gib']<512):
        raise RuntimeError('Exact8GPU/memory admission unavailable')
    affinity=hardware['cpu']['affinity'];cpus=affinity[64:] or affinity
    if not cpus:raise ValueError('Empty worker CPU affinity')
    env=dict(os.environ,OMP_NUM_THREADS='2',NUMEXPR_NUM_THREADS='2',RAYON_NUM_THREADS='1',
        TMPDIR=str(expected),SQLITE_TMPDIR=str(expected),PYTHONUNBUFFERED='1',
        CUDA_VISIBLE_DEVICES='0,1,2,3,4,5,6,7')
    env.pop(OWNER_ENV,None)
    return cpus,env


def prepare_attempt(control,runtime,contract,accepted,main):
    name='independent-'+uuid.uuid4().hex;folder=control/'attempts'/name
    folder.mkdir(parents=True,exist_ok=False)
    template=runtime['binding']['revision_template'];revision_dir=folder/'revision'
    result=prepare(main,template['path'],revision_dir,template_sha256=template['sha256'])
    if not accepted['parent_step']<=result['selection']['step']<57000:
        raise ValueError('Latest own checkpoint outside accepted resume interval')
    revision=revision_dir/'revision.json'
    admission=PreparedOperationalAdmission(main,revision,rank=0)
    runtime_sha=sha256_file(control/'runtime_contract.json')
    binding=dict(purpose='independent_main_attempt',runtime_contract_sha256=runtime_sha,attempt=name)
    shared=folder/'shared_admission.json';atomic_json(shared,admission.shared_record(binding))
    attempt=dict(purpose='independent_main_attempt_v1',attempt=name,main_run=str(main),world_size=8,
        runtime_contract=str(control/'runtime_contract.json'),runtime_contract_sha256=runtime_sha,
        revision=str(revision),revision_sha256=sha256_file(revision),
        training_contract_id=admission.candidate['id'],shared_admission=dict(path=str(shared),sha256=sha256_file(shared)),
        supervisor=self_record())
    path=folder/'execution.json';atomic_json(path,attempt)
    validate_attempt(path)
    (folder/'launch').mkdir()
    return path,attempt


def verify_live_consumption(execution,attempt,owned):
    directory=execution.parent/'consumption'
    paths=[directory/f'rank{rank}.json' for rank in range(8)]
    rows=[json.loads(p.read_text()) for p in paths]
    proof=verify_consumption_rows(attempt,rows,owner=owned.token)
    if json.loads((directory/'all_ranks.json').read_text())!=proof:
        raise ValueError('All-rank consumption marker differs from recomputation')
    if owned.proc.poll() is not None:raise ValueError('Launcher exited before live consumption admission')
    live=set(owned.scan());launch=process_record(owned.proc.pid)
    if launch['start_ticks']!=owned.records[owned.proc.pid]['start_ticks']:
        raise ValueError('Owned launcher identity changed')
    for row in rows:
        p=process_record(row['pid']);args=p['argv']
        if (p['pid'] not in live or p['start_ticks']!=row['start_ticks'] or p['ppid']!=owned.proc.pid or
                MODULE not in args or args.count('--execution')!=1 or
                resolve(args[args.index('--execution')+1])!=execution or
                row.get('launcher_pid')!=owned.proc.pid or row.get('launcher_start_ticks')!=launch['start_ticks']):
            raise ValueError('Consumption receipt does not match a live owned rank')
        environment=Path(f"/proc/{p['pid']}/environ").read_bytes().split(b'\0')
        if any(f'{key}={value}'.encode() not in environment for key,value in
                [(OWNER_ENV,owned.token),('RANK',row['rank']),('LOCAL_RANK',row['rank']),('WORLD_SIZE',8)]):
            raise ValueError('Live rank environment/ownership differs')
    return dict(proof=proof,receipts={str(p):sha256_file(p) for p in paths+[directory/'all_ranks.json']})


class CheckpointQueue:
    """One certification/copy queue; no mutation before live8rank consumption."""
    def __init__(self,main,contract,training_id,cancel):
        self.main,self.contract,self.training_id=main,contract,training_id
        self.manager=ExtendedRetention(main,contract,authorized=True,cancelled=cancel.is_set)
        self.pool=ThreadPoolExecutor(max_workers=1);self.future=None;self.job=None
        self.verified={};self.consumption=None;self.retained_count=-1

    def admit_existing(self):
        for cp in checkpoint_names(self.main):
            proof=json.loads((self.main/'checkpoint_certificates'/(cp.name+'.json')).read_text())
            if proof['training_contract_id']!=self.training_id or proof['step']!=int(cp.name[5:]):
                raise ValueError('Existing own checkpoint identity differs')
            inventory(cp,proof,hashes=True,workers=8);self.verified[cp.name]=proof

    def allow_retention(self,proof):
        if self.consumption is not None:raise ValueError('Consumption gate already set')
        self.consumption=proof

    def tick(self):
        if self.consumption is None:return
        for p,h in self.consumption['receipts'].items():
            if sha256_file(Path(p))!=h:raise ValueError('Consumed-state receipt changed')
        if self.future is not None:
            if not self.future.done():return
            result=self.future.result()
            if self.job[0]=='seal':self.verified[self.job[1]]=result
            self.future=None;self.job=None
        from train.runtime import complete_checkpoints
        if json.loads((self.main/'training/training_contract.json').read_text())['id']!=self.training_id:
            raise ValueError('Original training identity changed')
        for cp in complete_checkpoints(self.main/'training'):
            if cp.name not in self.verified:
                step=int(cp.name[5:])
                if not self.consumption['proof']['step']<step<=57000 or step%500:
                    raise ValueError('Unexpected own checkpoint boundary')
                self.job=('seal',cp.name)
                self.future=self.pool.submit(seal,cp,self.main/'checkpoint_certificates'/(cp.name+'.json'),self.training_id)
                return
        if self.retained_count!=len(self.verified):
            self.retained_count=len(self.verified);self.job=('retention',None)
            self.future=self.pool.submit(self.manager.maintain)

    def drain(self):
        if self.consumption is None:raise ValueError('No actual all8rank consumption; cannot finalize or prune')
        while True:
            self.tick()
            if self.future is None:break
            self.future.result()

    def close(self):self.pool.shutdown(wait=True,cancel_futures=True)


def runtime_guard(main,contract,control,execution,execution_sha,elapsed,owned,queue):
    if sha256_file(execution)!=execution_sha:raise ValueError('Attempt execution changed')
    attempt=json.loads(execution.read_text())
    if sha256_file(control/'runtime_contract.json')!=attempt['runtime_contract_sha256']:
        raise ValueError('Runtime contract changed during training')
    runtime=json.loads((control/'runtime_contract.json').read_text());binding=runtime['binding']
    for p,h in [(main/'contract.json',binding['main_contract_sha256']),(main/'main.json',binding['main_config_sha256'])]:
        if sha256_file(p)!=h:raise ValueError('Main execution contract changed during training')
    if elapsed+binding['elapsed_before_seconds']>contract['max_seconds']:
        raise RuntimeError('Main elapsed budget exceeded')
    # Include controller/worker threads and any owned detached descendants.
    from scripts.process_accounting import proportional_tree,parse_rollup
    observation=proportional_tree(os.getpid());pss=observation['totals_bytes']['Pss']
    counted={p['pid'] for p in observation['processes']}
    for pid in set(owned.scan())-counted:
        try:pss+=parse_rollup(Path(f'/proc/{pid}/smaps_rollup').read_text())['Pss']
        except (FileNotFoundError,ProcessLookupError):continue
    owned.peak=max(owned.peak,pss)
    if pss>contract['max_pss_gib']*GIB:raise RuntimeError('Main proportional memory budget exceeded')
    scratch=Path(contract['tiered_retention']['temp_root'])
    if (size_tree(main)>contract['durable_budget_bytes'] or size_tree(scratch)>contract['local_budget_gib']*GIB or
            min(shutil.disk_usage(main).free,shutil.disk_usage(scratch).free)<200*GIB):
        raise RuntimeError('Main disk/reserve budget exceeded')
    busy=queue.future is not None and not queue.future.done()
    # Before consumption, fail closed without pruning even under pressure.
    from scripts.horizon_storage_plan import LocalHistoryCapacityError
    try:space=enforce_shared_space(main,contract,local_copy_busy=busy)
    except LocalHistoryCapacityError:
        if queue.consumption is None or busy:raise
        with queue.manager.locked():queue.manager.make_local_room()
        space=enforce_shared_space(main,contract)
    atomic_json(control/'resources_latest.json',dict(elapsed_seconds=elapsed,pss_bytes=pss,space=space))
    return pss


def monitor(owned,execution,attempt,main,contract,control,queue,cancel,*,elapsed_before_attempt=0):
    started=time.monotonic();last_guard=-30;execution_sha=sha256_file(execution)
    peak=0;consumed=False
    try:
        while True:
            elapsed=time.monotonic()-started
            if elapsed-last_guard>=30:
                peak=max(peak,runtime_guard(main,contract,control,execution,execution_sha,elapsed+elapsed_before_attempt,owned,queue));last_guard=elapsed
            if not consumed:
                if (execution.parent/'consumption/all_ranks.json').exists():
                    proof=verify_live_consumption(execution,attempt,owned)
                    atomic_json(execution.parent/'consumption/supervisor_verified.json',proof)
                    queue.allow_retention(proof);consumed=True
                elif elapsed>600:raise RuntimeError('Own-state all8rank consumption deadline exceeded')
            queue.tick()
            rc=owned.proc.poll()
            if rc is not None:
                if rc:raise RuntimeError('Main owned worker exited '+str(rc))
                if owned.scan():raise RuntimeError('Main launcher exited with live owned descendants')
                queue.drain()
                result=dict(status='owned_main_exited_successfully',exit_code=0,owner=owned.token,
                    elapsed_seconds=elapsed,peak_pss_bytes=peak,processes=list(owned.records.values()),remaining_owned_processes=[])
                atomic_json(execution.parent/'launch/result.json',result)
                return result
            atomic_json(main/'state.json',dict(phase='running',case='independent_main',worker_pid=owned.proc.pid,
                elapsed_seconds=elapsed,peak_pss_bytes=peak,independent_execution=str(execution),all8_consumed=consumed))
            time.sleep(2)
    except BaseException as exc:
        cancel.set();owned.event('command_failed',error=repr(exc))
        try:owned.cleanup()
        finally:atomic_json(execution.parent/'launch/failure.json',dict(error=repr(exc),owner=owned.token,
            elapsed_seconds=time.monotonic()-started,outputs_preserved=True))
        raise
    finally:owned.close()


def elapsed_clock(control,runtime_sha):
    """Persist elapsed horizon across failed attempts; downtime still counts."""
    path=control/'elapsed_clock.json';now=time.time()
    if not path.exists():
        atomic_json(path,dict(runtime_contract_sha256=runtime_sha,started_unix=now))
    value=json.loads(path.read_text());started=value['started_unix']
    if (value['runtime_contract_sha256']!=runtime_sha or type(started) not in (int,float) or
            not math.isfinite(started) or not 0<started<=now):
        raise ValueError('Runtime elapsed clock changed or time moved backward')
    return now-started


def run(control):
    control=exact_path(resolve(control));runtime,contract,accepted,main=runtime_inputs(control)
    with exclusive_controls(main,control):
        require_absent(main,control,runtime['binding'])
        if (main/'.done').exists():raise ValueError('Main target already published; use final verification, never train again')
        from scripts.resource_admission import inspect
        from scripts.evaluate_final_rehearsal import acquire_idle_gpu
        hardware=inspect();cpus,env=execution_settings(contract,main,hardware)
        enforce_shared_space(main,contract)
        cancel=threading.Event();queue=None;owned=None;start=time.monotonic()
        def waiting(phase):
            if time.monotonic()-start>600:raise RuntimeError('GPU admission deadline exceeded; preserve existing processes')
            atomic_json(control/'state.json',dict(phase='waiting',case=phase))
        try:
            with acquire_idle_gpu(waiting):
                require_absent(main,control,runtime['binding'])
                # Re-admit after waiting; no settings or evidence may drift.
                runtime,contract,accepted,main=runtime_inputs(control)
                execution,attempt=prepare_attempt(control,runtime,contract,accepted,main)
                queue=CheckpointQueue(main,contract,attempt['training_contract_id'],cancel);queue.admit_existing()
                enforce_shared_space(main,contract)
                Path(env['TMPDIR']).mkdir(parents=True,exist_ok=True)
                command=['taskset','-c',','.join(map(str,cpus)),sys.executable,'-m','torch.distributed.run',
                    '--standalone','--nproc_per_node=8','--module',MODULE,'--execution',str(execution)]
                atomic_json(execution.parent/'hardware.json',hardware)
                elapsed_before_attempt=elapsed_clock(control,attempt['runtime_contract_sha256'])
                owned=OwnedCommand(command,execution.parent/'launch',env)
                atomic_json(control/'current_attempt.json',dict(execution=str(execution),execution_sha256=sha256_file(execution),runtime_contract_sha256=attempt['runtime_contract_sha256']))
                (main/'pid').write_text(str(os.getpid())+'\n')
                result=monitor(owned,execution,attempt,main,contract,control,queue,cancel,elapsed_before_attempt=elapsed_before_attempt)
                final=main/'training/ckpt_0057000'
                if final.name not in queue.verified:raise ValueError('Final57000 certified checkpoint missing')
                inventory(final,queue.verified[final.name],hashes=True,workers=8)
                report=dict(status='main_target_completed_review_pending',final_checkpoint=artifact_path(final),
                    trained_tokens=119537664000,execution=str(execution),runtime_contract_sha256=attempt['runtime_contract_sha256'],
                    elapsed_seconds=result['elapsed_seconds'],
                    runtime_elapsed_seconds=elapsed_before_attempt+result['elapsed_seconds'],
                    reviewed_prior_elapsed_seconds=runtime['binding']['elapsed_before_seconds'],
                    peak_pss_bytes=result['peak_pss_bytes'],
                    certificate_receipts={p.name:sha256_file(p) for p in (main/'checkpoint_certificates').glob('*.json')},
                    retention=json.loads((main/'retention_state.json').read_text()),
                    scope='Target checkpoint saved; committed ledger audit/final native/actualGPU restore/packaging remain separate.')
                atomic_json(main/'report.json',report)
                atomic_json(main/'.done',dict(report_sha256=sha256_file(main/'report.json'),contract_sha256=sha256_file(main/'contract.json')))
                atomic_json(main/'state.json',dict(phase='complete',case='target_saved_final_review_pending'))
                atomic_json(control/'state.json',dict(phase='complete',execution=str(execution)))
                return report
        except BaseException as exc:
            cancel.set();atomic_json(control/'state.json',dict(phase='error',error=repr(exc),outputs_preserved=True))
            # Only advertise a main failure if this controller launched its own worker.
            if owned is not None:atomic_json(main/'state.json',dict(phase='error',error=repr(exc)))
            raise
        finally:
            try:
                if owned is not None and not owned.journal.closed:
                    try:owned.cleanup()
                    finally:owned.close()
            finally:
                if queue is not None:queue.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--control-dir',required=True);p.add_argument('--run',required=True,action='store_true')
    run(p.parse_args().control_dir)
