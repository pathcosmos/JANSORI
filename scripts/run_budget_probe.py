"""Bounded four-mode8GPU horizon diagnostic, never a main-training launcher.

Requires the prepared sealed-parent bundle, actual idle GPUs, strict aggregate
capacity, and explicit --execute. Every subprocess is pidfd/tag-owned. Failure
preserves outputs; no automatic retry or checkpoint pruning is performed.
"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve, identity, sha256_file
from scripts.tiered_checkpoint_retention import exact_path, atomic_json, inventory
from scripts.seal_horizon_handoff import unchanged, require_parent_absent
from scripts.extended_horizon_storage import enforce_shared_space as enforce_runtime_space, GIB
from scripts.horizon_probe_process import run_command
from scripts.prepare_budget_probe import resume_spec
from scripts.verify_horizon_probe import certify_checkpoint, compare_source_ledgers
from scripts.verify_horizon_observations import compare_observations
from scripts.verify_final_resume_probe import compare_states, verify_inventory
from train.budget_resume_probe import validate_plan, check_spec
from train.budget_horizon_contract import bound_candidate
from train.final_resume_probe import file_stats

MODES=('baseline','continuous','first','resumed')
STAGES=('baseline','continuous','certify-continuous','first','certify-first',
        'resumed','certify-resumed','compare')


def load_plan(path):
    path=exact_path(resolve(path));plan=json.loads(path.read_text());validate_plan(plan)
    root=exact_path(resolve(plan['main_root']))/'horizon_probe'
    if path!=root/'plan.json' or plan.get('max_seconds')!=3600 or plan.get('max_pss_gib')!=384:
        raise ValueError('Unreviewed diagnostic controller path/time/memory budget')
    required={'scripts/run_budget_probe.py','scripts/horizon_probe_process.py',
              'scripts/prepare_budget_probe.py','scripts/verify_horizon_probe.py',
              'scripts/verify_horizon_observations.py','scripts/verify_final_resume_probe.py'}
    if not required<=plan['code'].keys():raise ValueError('Unbound diagnostic controller/verifier')
    unchanged({**plan['code'],**plan['inputs']})
    for key in ('draft','capture'):
        p=exact_path(resolve(plan[key]))
        if plan['inputs'].get(str(p))!=sha256_file(p):raise ValueError('Unbound controller '+key)
    receipt=json.loads((root/'preparation.json').read_text())
    if (receipt['status']!='prepared_bounded_budget_probe_not_executed' or
            receipt['plan_id']!=identity(plan)):
        raise ValueError('Diagnostic preparation is incomplete')
    return plan,root


def command_for(plan_path,phase):
    if phase not in STAGES:raise ValueError('Unknown diagnostic phase')
    plan_path=exact_path(resolve(plan_path))
    if phase in MODES:
        return [sys.executable,'-m','torch.distributed.run','--standalone','--nproc_per_node=8',
                '--module','train.budget_resume_probe','--spec',str(plan_path.parent/(phase+'.json')),
                '--mode',phase]
    return [sys.executable,'-m','scripts.run_budget_probe','--plan',str(plan_path),'--phase',phase]


def checkpoint_targets(plan,mode):
    if mode not in MODES or mode=='baseline':return []
    root=exact_path(resolve(plan['modes'][mode]['run_dir']))
    receipt=json.loads((root/'parent_handoff.json').read_text())
    candidate=bound_candidate(receipt['parent_metadata'],receipt['execution_binding'])
    config=json.loads(resolve(plan['modes'][mode]['config']).read_text())
    step=plan['parent_step']+(1 if mode=='first' else 2)
    meta=dict(step=step,config=config,sampler_version=config['sampler_version'],
        training_contract=candidate,tokens_per_step=2097152,world_size=8)
    result=[(resolve(config['out_dir'])/f'ckpt_{step:07d}',meta,root/'certificates'/(mode+'.json'))]
    if mode=='continuous':
        restored=dict(kind='horizon_restored_parent',step=plan['parent_step'],world_size=8,
            diagnostic_only=True,plan_id=identity(plan),parent_contract_id=plan['parent_contract_id'],
            child_contract_id=plan['child_contract_id'])
        result.insert(0,(resolve(config['out_dir']).parent/'restored_parent',restored,root/'certificates/restored_parent.json'))
    return result


def verify_certificates(plan):
    proofs={}
    for mode in ('continuous','first','resumed'):
        for path,meta,cert in checkpoint_targets(plan,mode):
            proof=json.loads(cert.read_text())
            if (proof.get('status')!='certified_horizon_probe_checkpoint' or
                    proof.get('cuda_rng_required') is not True or proof['metadata']!=meta or
                    exact_path(resolve(proof['checkpoint']))!=path):
                raise ValueError('Diagnostic certificate metadata/path differs')
            verify_inventory(path,proof)
            proofs['restored_parent' if path.name=='restored_parent' else mode]=(path,cert,proof)
    return proofs


def require_comparison(result):
    if (result.get('exact') is not True or result.get('model_tensors')!=255 or
            result.get('optimizer_tensors')!=765 or result.get('tensors')!=1020 or
            result.get('rng_ranks')!=8):
        raise ValueError('Full3B model/optimizer/8rank RNG comparison missing')


def cpu_phase(plan_path,phase):
    plan,root=load_plan(plan_path)
    if phase.startswith('certify-'):
        mode=phase.removeprefix('certify-')
        if mode not in ('continuous','first','resumed'):raise ValueError('Unknown certificate phase')
        for path,meta,cert in checkpoint_targets(plan,mode):certify_checkpoint(path,meta,cert)
    elif phase=='compare':
        proofs=verify_certificates(plan)
        continuous=resolve(plan['roots']['continuous'])
        receipt=json.loads((continuous/'parent_handoff.json').read_text())
        parent=exact_path(resolve(receipt['parent_checkpoint']))
        proof=json.loads((continuous/'parent_certificate.json').read_text())
        inventory(parent,proof)
        if file_stats(parent)!=receipt['parent_stats']:raise ValueError('Protected parent changed')
        comparisons=dict(parent_restored=compare_states(parent,proofs['restored_parent'][0],world=8),
                         continuous_resumed=compare_states(proofs['continuous'][0],proofs['resumed'][0],world=8))
        for value in comparisons.values():require_comparison(value)
        verify_certificates(plan)
        if file_stats(parent)!=receipt['parent_stats']:raise ValueError('Parent changed during comparison')
        output=root/'comparisons.json'
        if output.exists():raise ValueError('Preserve previous state comparison')
        atomic_json(output,dict(plan_id=identity(plan),**comparisons))
    else:raise ValueError('Only certificate/comparison CPU phases are allowed')


def validate_runtime(plan,spec,mode,launch,rows):
    """Require real initialized NCCL rank receipts linked to observed processes."""
    pidmap={r['pid']:r for r in launch['processes']}
    if (len(pidmap)!=len(launch['processes']) or len(rows)!=8 or
            launch['status']!='command_exited_successfully' or launch['exit_code']!=0 or
            launch['remaining_owned_processes']!=[] or
            [r['pid'] for r in launch['processes'] if r['launcher']] != [launch['launcher_pid']]):
        raise ValueError('Incomplete successful diagnostic process coverage')
    ranks=set();pids=set();uuids=[]
    for row in rows:
        rank=row['rank'];pid=row['pid']
        if (type(rank) is not int or not 0<=rank<8 or rank in ranks or pid in pids or
                row['plan_id']!=identity(plan) or row['spec_id']!=identity(spec) or row['mode']!=mode or
                row['world_size']!=8 or row['backend']!='nccl' or row['local_rank']!=rank or
                'H200' not in row['device_name'] or row['device_total_memory']<130*GIB or
                row['framework']!=plan['framework'] or row['diagnostic_only'] is not True or
                row['parent_pid']!=launch['launcher_pid'] or pid not in pidmap or
                pidmap[pid]['start_ticks']!=row['start_ticks'] or pidmap[pid]['launcher']):
            raise ValueError('Actual8GPU provenance differs from owned launcher/spec')
        ranks.add(rank);pids.add(pid)
        if row.get('device_uuid') is not None:uuids.append(row['device_uuid'])
    if uuids and (len(uuids)!=8 or len(set(uuids))!=8):
        raise ValueError('Actual rank devices are not distinct')


def collect(plan_path):
    from train.train import lr_at
    from scripts.audit_main_training import Snapshot
    plan,root=load_plan(plan_path);proofs=verify_certificates(plan)
    observations=[];ledgers=[];evidence={str(resolve(plan_path)):sha256_file(resolve(plan_path)),
                                      **plan['code'],**plan['inputs']}
    snapshot=Snapshot()
    def read(path):
        return snapshot.read(exact_path(path))
    if read(root/'execution_started.json')['plan_id']!=identity(plan):
        raise ValueError('Controller launch belongs to another diagnostic plan')
    hardware=read(root/'execution_hardware.json')
    draft=json.loads(resolve(plan['draft']).read_text())
    if len(hardware['gpus'])!=8 or hardware['gpus']!=draft['gpu_identity']:
        raise ValueError('Controller hardware differs from the reviewed devices')
    main_receipt=read(resolve(plan['main_root'])/'parent_handoff.json')
    parent=main_receipt['parent_metadata']
    candidate=bound_candidate(parent,main_receipt['execution_binding'])
    if (parent['step']!=plan['parent_step'] or parent['training_contract']['id']!=plan['parent_contract_id'] or
            candidate['id']!=plan['child_contract_id']):
        raise ValueError('Diagnostic identity differs from protected main parent')
    protected=[]
    parent_proof=read(resolve(plan['main_root'])/'parent_certificate.json')
    for branch in [resolve(plan['main_root']),resolve(plan['roots']['continuous']),resolve(plan['roots']['split'])]:
        receipt=read(branch/'parent_handoff.json');proof=read(branch/'parent_certificate.json')
        path=exact_path(resolve(receipt['parent_checkpoint']))
        if (path!=resolve(plan['main_root'])/'parent_checkpoint'/f"ckpt_{plan['parent_step']:07d}" or proof!=parent_proof or
                receipt['parent_metadata']!=parent or receipt['execution_binding']!=main_receipt['execution_binding']):
            raise ValueError('Protected branches no longer share the exact parent')
        inventory(path,proof)
        if file_stats(path)!=receipt['parent_stats']:raise ValueError('Protected parent changed before acceptance')
        protected.append((path,receipt['parent_stats']))
    devices={}
    for stage in STAGES:
        launch=read(root/'commands'/stage/'result.json')
        handle=read(root/'commands'/stage/'handle.json')
        if (launch['command']!=command_for(plan_path,stage) or launch['exit_code']!=0 or
                launch['status']!='command_exited_successfully' or launch['remaining_owned_processes']!=[] or
                handle['pid']!=launch['launcher_pid'] or handle['owner']!=launch['owner'] or
                handle['command']!=launch['command'] or handle['process'] not in launch['processes']):
            raise ValueError('Diagnostic phase did not finish under the bound controller')
        if stage not in MODES:continue
        row=plan['modes'][stage];branch=exact_path(resolve(row['run_dir']))
        spec=read(root/(stage+'.json'))
        if spec['plan']!=plan or spec['plan_id']!=identity(plan):raise ValueError('Executed spec differs')
        actual_contract=read(resolve(plan['payload_roots'][stage])/'training/training_contract.json')
        if actual_contract!=(parent['training_contract'] if stage=='baseline' else candidate):
            raise ValueError('Actual native training contract differs from original/child binding')
        if stage=='resumed':
            _,cert,proof=proofs['first']
            if (resolve(spec['resume_certificate'])!=cert or spec['resume_certificate_sha256']!=sha256_file(cert) or
                    spec['resume_stats']!=proof['stats']):
                raise ValueError('Executed resume specification used another intermediate checkpoint')
        runtime=[read(branch/'execution'/f'{stage}-rank{rank}.json') for rank in range(8)]
        validate_runtime(plan,spec,stage,launch,runtime)
        current={r['rank']:r.get('device_uuid') for r in runtime}
        if devices and current!=devices:raise ValueError('Diagnostic rank devices changed between modes')
        devices=current
        config=json.loads(resolve(row['config']).read_text())
        for rank in range(8):
            obs=read(branch/'observations'/f'{stage}-rank{rank}.json')
            for value in [obs['initial']['state'],*[u['state'] for u in obs['updates']]]:
                if value['model']['tensors']!=255 or value['optimizer']['tensors']!=765:
                    raise ValueError('Observed states do not cover full3B model/optimizer')
            if any(u['lr']!=lr_at(u['step']-1,config) for u in obs['updates']):
                raise ValueError('Actual diagnostic LR differs from reviewed schedule')
            observations.append(obs)
            ledgers.append((stage,read(branch/'ledgers'/(row['attempt']+f'-rank{rank}.json'))))
    observation_result=compare_observations(observations,parent_step=plan['parent_step'],plan_id=identity(plan))
    ledger_result=compare_source_ledgers(ledgers,parent_step=plan['parent_step'],
        parent_contract_id=plan['parent_contract_id'],child_contract_id=plan['child_contract_id'],
        sources=parent['training_contract']['source_order'])
    comparisons=read(root/'comparisons.json')
    if comparisons['plan_id']!=identity(plan):raise ValueError('Independent comparison belongs to another plan')
    for name in ('parent_restored','continuous_resumed'):require_comparison(comparisons[name])
    for _,cert,_ in proofs.values():read(cert)
    evidence.update(snapshot.bindings)
    verify_certificates(plan)
    if any(file_stats(path)!=stats for path,stats in protected):raise ValueError('Protected parent changed during acceptance')
    snapshot.unchanged()
    unchanged(evidence)
    result=dict(status='passed_actual_8rank_budget_resume',world_size=8,
        parent_step=plan['parent_step'],parent_contract_id=plan['parent_contract_id'],
        child_contract_id=plan['child_contract_id'],plan_id=identity(plan),
        model_optimizer_rng_exact=True,next_update_exact=True,split_resume_exact=True,
        observations=observation_result,source_ledger=ledger_result,comparisons=comparisons,
        evidence=evidence,main_committed_tokens_added=0,main_target_modified=False)
    if (root/'report.json').exists():raise ValueError('Preserve prior complete diagnostic report')
    atomic_json(root/'report.json',result)
    return result


def gpu_pids():
    raw=subprocess.check_output(['nvidia-smi','--query-compute-apps=pid','--format=csv,noheader'],
                                text=True,timeout=10).strip()
    return {int(line.strip()) for line in raw.splitlines() if line.strip()}


@contextmanager
def idle_gpu_lease():
    with resolve('runs/.gpu_benchmark.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if gpu_pids():raise RuntimeError('Diagnostic requires all8GPUs idle; no process was signalled')
        yield


def execute(plan_path):
    from scripts.resource_admission import inspect
    plan,root=load_plan(plan_path)
    draft=json.loads(resolve(plan['draft']).read_text())
    capture=json.loads(resolve(plan['capture']).read_text())
    main=exact_path(resolve(plan['main_root']));source=exact_path(resolve(draft['source_run']))
    require_parent_absent(capture,source)
    if any((root/name).exists() for name in ('execution_started.json','commands','report.json','failure.json')):
        raise ValueError('Prior diagnostic execution exists; inspect preserved handles/results before recovery')
    hw=inspect()
    if (len(hw['gpus'])!=8 or hw['gpus']!=draft['gpu_identity'] or
            hw['memory']['cache_aware_admission']['headroom_gib']<512):
        raise RuntimeError('Actual hardware or cache-aware memory admission differs')
    enforce_runtime_space(main,draft)
    started=time.monotonic()
    with (root/'controller.lock').open('a') as lock, idle_gpu_lease():
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        require_parent_absent(capture,source)
        atomic_json(root/'execution_hardware.json',hw)
        atomic_json(root/'execution_started.json',dict(plan_id=identity(plan),pid=os.getpid(),time_ns=time.time_ns()))
        prior=signal.getsignal(signal.SIGTERM)
        def terminated(*args):raise RuntimeError('Diagnostic controller received SIGTERM')
        signal.signal(signal.SIGTERM,terminated)
        try:
            (root/'commands').mkdir()
            scratch=exact_path(Path(draft['tiered_retention']['temp_root'])/'horizon_probe')
            scratch.mkdir(parents=True,exist_ok=True)
            env=dict(os.environ,OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',NUMEXPR_NUM_THREADS='2',
                RAYON_NUM_THREADS='1',TMPDIR=str(scratch),SQLITE_TMPDIR=str(scratch),PYTHONUNBUFFERED='1')
            for phase in STAGES:
                load_plan(plan_path);require_parent_absent(capture,source)
                if phase=='resumed':resume_spec(plan_path)
                if phase in MODES:check_spec(json.loads((root/(phase+'.json')).read_text()),phase)
                directory=root/'commands'/phase;directory.mkdir()
                env['CUDA_VISIBLE_DEVICES']='0,1,2,3,4,5,6,7' if phase in MODES else ''
                def guard(owned,elapsed,peak):
                    if time.monotonic()-started>plan['max_seconds']:raise TimeoutError('Total diagnostic time budget exceeded')
                    if peak>plan['max_pss_gib']*GIB:raise RuntimeError('Diagnostic process PSS budget exceeded')
                    space=enforce_runtime_space(main,draft)
                    foreign=gpu_pids()-set(owned.fds)
                    if foreign:
                        # A rank may initialize CUDA between memory sampling and
                        # nvidia-smi. Re-discover this launch's inherited tag.
                        owned.scan()
                        foreign-=set(owned.fds)
                    if foreign:raise RuntimeError('Unexpected GPU owner during isolated diagnostic')
                    atomic_json(root/'state.json',dict(phase=phase,elapsed_seconds=time.monotonic()-started,
                        launcher_pid=owned.proc.pid,peak_pss_bytes=peak,space=space))
                run_command(command_for(plan_path,phase),directory,env,guard)
            result=collect(plan_path)
            atomic_json(root/'.done',dict(plan_id=identity(plan),report_sha256=sha256_file(root/'report.json')))
            atomic_json(root/'state.json',dict(phase='complete',elapsed_seconds=time.monotonic()-started))
            return result
        except BaseException as exc:
            atomic_json(root/'failure.json',dict(error=repr(exc),outputs_preserved=True,
                main_target_modified=False,elapsed_seconds=time.monotonic()-started))
            raise
        finally:signal.signal(signal.SIGTERM,prior)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',required=True)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--execute',action='store_true')
    group.add_argument('--phase',choices=[p for p in STAGES if p not in MODES])
    args=parser.parse_args()
    if args.execute:execute(args.plan)
    else:cpu_phase(args.plan,args.phase)
