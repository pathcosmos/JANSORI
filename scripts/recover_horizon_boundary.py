"""Prepare or explicitly execute the failed8000 boundary's limited recovery.

Execution requires a new approval bound to the prepared plan. It terminates
only the exact nine paused survivors and seals the verified parent. It does not
launch training/diagnostics, delete data, or retry a failed execution directory.
"""
import argparse
import fcntl
import json
import os
import signal
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve, sha256_file, identity, atomic_json
from scripts.horizon_process_handoff import BoundProcessSet, process_record, same_identity, children
from scripts.horizon_exit_wait import terminate_stopped, wait_exit_only
from scripts.horizon_storage_plan import enforce_runtime_space, GIB
from scripts.seal_horizon_handoff import (unchanged, write_once, json_bytes,
    prepare_lineage, seal_handoff, require_parent_absent)
from scripts.tiered_checkpoint_retention import exact_path, inventory, size_tree
from train.final_resume_probe import file_stats

SCOPE = 'finish_verified_paused_boundary_and_seal_only'
ROLES = {'worker', *[f'rank{r}' for r in range(8)]}


def check_approval(plan_path, approval_path):
    approval = json.loads(exact_path(resolve(approval_path)).read_text())
    if (approval.get('authorized') is not True or approval.get('scope') != SCOPE
            or approval.get('plan_sha256') != sha256_file(plan_path)
            or approval.get('target_steps') != 35000 or approval.get('parent_step') != 8000
            or approval.get('launch_training') is not False
            or not isinstance(approval.get('user_decision'), str) or not approval['user_decision'].strip()):
        raise ValueError('Require explicit new user decision bound to this recovery plan')
    return approval


def absent_identity(row):
    try:
        ticks = int(Path(f"/proc/{row['pid']}/stat").read_text().rsplit(')',1)[1].split()[19])
    except FileNotFoundError:
        return
    if ticks == row['start_ticks']:
        raise ValueError('Expected terminated controller remains present')


def check_survivors(capture, source):
    absent_identity(capture['processes']['supervisor'])
    rows = {name: capture['processes'][name] for name in ROLES}
    if len({row['pid'] for row in rows.values()}) != 9:
        raise ValueError('Require exact nine distinct original survivors')
    current = {}
    for name,row in rows.items():
        now = process_record(row['pid'])
        if not same_identity(row,now) or now['state'] not in ('T','t'):
            raise ValueError('Surviving producer identity/stopped state differs: '+name)
        current[name] = now
    worker = current['worker']['pid']; ranks = {current[f'rank{r}']['pid'] for r in range(8)}
    if children(worker) != ranks or any(children(pid) for pid in ranks):
        raise ValueError('Surviving process topology differs')
    if any(current[f'rank{r}']['ppid'] != worker for r in range(8)):
        raise ValueError('Rank parent differs')
    allowed = {row['pid'] for row in current.values()}
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) in allowed:
            continue
        try:
            args = [s.decode() for s in (proc/'cmdline').read_bytes().split(b'\0') if s]
            if '--run-dir' not in args:
                continue
            i = args.index('--run-dir')
            if i+1 < len(args) and ((proc/'cwd').resolve(strict=True)/args[i+1]).resolve() == source:
                raise ValueError('Another process owns original namespace: '+proc.name)
        except (FileNotFoundError,ProcessLookupError):
            continue
    return current


def inspect_context(original_path, *, hashes):
    from scripts.transition_horizon_training import read_plan
    original, transition = read_plan(original_path)
    if original['boundary_step'] != 8000:
        raise ValueError('This recovery is only for the reviewed8000 failure')
    failure = json.loads((transition/'failure.json').read_text())
    boundary = json.loads((transition/'boundary/state.json').read_text())
    if (failure['phase'] != 'protect_and_stop_original'
            or 'Process command/identity changed' not in failure['error']
            or boundary['phase'] != 'protected_parent_verified'):
        raise ValueError('Different boundary failure requires separate review')
    source = exact_path(resolve(original['source'])); destination = exact_path(resolve(original['destination']))
    capture = json.loads(resolve(original['capture']).read_text())
    for row in original['waiting_controllers'].values():
        absent_identity(row)
    survivors = check_survivors(capture, source)
    if any((destination/name).exists() for name in ['parent_handoff.json','contract.json','lineage','lineage_candidate','horizon_probe']):
        raise ValueError('Preserve existing later-stage evidence; this is not an adoption tool')
    draft = json.loads(resolve(original['draft']).read_text())
    owner_path = destination/'handoff_owner.json'; owner = json.loads(owner_path.read_text())
    if (owner['owner_id'] != draft['tiered_retention']['run_id']
            or owner['plan_sha256'] != sha256_file(transition/'boundary_plan.json')):
        raise ValueError('Existing protected parent has another owner')
    proof_path = source/'checkpoint_certificates/ckpt_0008000.json'
    proof = json.loads(proof_path.read_text()); cp = source/'training/ckpt_0008000'
    protected = destination/'parent_checkpoint/ckpt_0008000'
    receipt_path = protected.with_name(protected.name+'.receipt.json')
    receipt = json.loads(receipt_path.read_text()); tx = receipt['transaction']
    if (receipt != boundary['protected'] or receipt['status'] != 'verified_protected_parent'
            or tx['owner_id'] != owner['owner_id'] or tx['source'] != str(cp)
            or tx['destination'] != str(protected) or tx['proof_id'] != identity(proof)
            or receipt['proof_id'] != identity(proof) or tx['step'] != 8000 or tx['bytes'] != proof['bytes']):
        raise ValueError('Protected parent receipt/identity differs')
    capacity = enforce_runtime_space(destination,draft)
    inventory(cp,proof,hashes=hashes,workers=8)
    inventory(protected,proof,hashes=hashes,workers=8)
    if receipt['stats'] != file_stats(protected):
        raise ValueError('Protected parent changed since original full verification')
    check_survivors(capture,source)
    bindings = {**original['code'], **original['inputs']}
    for path in [exact_path(resolve(original_path)), transition/'boundary_plan.json', transition/'failure.json',
                 transition/'boundary/state.json', transition/'boundary/failure.json', owner_path, proof_path, receipt_path]:
        bindings[str(path)] = sha256_file(path)
    return dict(original=original,transition=transition,source=source,destination=destination,
                capture=capture,survivors=survivors,draft=draft,owner=owner,bindings=bindings,capacity=capacity)


def prepare(original_path):
    ctx = inspect_context(original_path,hashes=True)
    review = ctx['transition']/'recovery_review_v1'; review.mkdir(exist_ok=True)
    path = review/'execution_plan.json'
    if path.exists() or (ctx['transition']/'recovery_execution_v1').exists():
        raise ValueError('Preserve existing recovery plan/execution')
    code = {name:sha256_file(resolve(name)) for name in ['scripts/recover_horizon_boundary.py','scripts/horizon_exit_wait.py']}
    plan = dict(purpose=SCOPE,original_plan=str(exact_path(resolve(original_path))),target_steps=35000,parent_step=8000,
                output=str(ctx['transition']/'recovery_execution_v1'),bindings=ctx['bindings'],code=code,
                survivors=ctx['survivors'],max_seconds=600,max_pss_gib=16,termination_grace_seconds=30,
                durable_scope=str(ctx['transition']),durable_cap_bytes=GIB,launch_training=False,automatic_retry=False)
    write_once(path,json_bytes(plan))
    write_once(review/'approval_required.json',json_bytes(dict(authorized=False,scope=SCOPE,
        plan_sha256=sha256_file(path),target_steps=35000,parent_step=8000,launch_training=False,user_decision='')))
    atomic_json(review/'prepared_capacity.json',ctx['capacity'])
    return path


def transaction(rows, output, *, authorized, precheck, verify_parents, after_stop, guard, execution_binding=None):
    if authorized is not True or set(rows) != ROLES:
        raise ValueError('Explicit authorization and exact nine roles required')
    if output.exists():
        raise ValueError('Preserve failed or completed recovery; no automatic retry')
    precheck();guard();output.mkdir()
    (output/'pid').write_text(str(os.getpid())+'\n')
    if execution_binding is not None:
        atomic_json(output/'execution_binding.json',execution_binding)
    phase='binding'
    try:
        with BoundProcessSet(rows,output/'signals.jsonl') as bound:
            precheck();verify_parents();guard()
            phase='verified_parents_before_termination'
            atomic_json(output/'state.json',dict(phase='running',case=phase))
            for name in [*[f'rank{r}' for r in range(8)],'worker']:
                guard();phase='terminate_'+name
                atomic_json(output/'state.json',dict(phase='running',case=phase))
                terminate_stopped(bound,name,grace_seconds=30)
            wait_exit_only(bound,rows,timeout=5)
        guard();phase='seal_original8000_prefix'
        atomic_json(output/'state.json',dict(phase='running',case=phase))
        result=after_stop();guard()
        atomic_json(output/'report.json',dict(status='recovered_sealed8000_boundary_not_training',
            result=result,launch_training=False,actual8gpu_verified=False))
        proof=dict(report_sha256=sha256_file(output/'report.json'))
        if execution_binding is not None:proof['execution_binding_sha256']=sha256_file(output/'execution_binding.json')
        atomic_json(output/'.done',proof)
        atomic_json(output/'state.json',dict(phase='complete',case='sealed_parent_diagnostic_pending'))
        return result
    except BaseException as exc:
        atomic_json(output/'failure.json',dict(phase=phase,error=repr(exc),outputs_preserved=True,automatic_retry=False))
        atomic_json(output/'state.json',dict(phase='error',case=phase,error=str(exc)))
        raise


def execute(plan_path, approval_path):
    plan_path=exact_path(resolve(plan_path)); plan=json.loads(plan_path.read_text())
    approval=check_approval(plan_path,approval_path)  # Before creation, locks, or signals.
    if (plan['purpose']!=SCOPE or plan['target_steps']!=35000 or plan['parent_step']!=8000
            or plan['max_seconds']!=600 or plan['max_pss_gib']!=16 or plan['termination_grace_seconds']!=30
            or plan['launch_training'] is not False or plan['durable_cap_bytes']!=GIB):
        raise ValueError('Unreviewed recovery limits/scope')
    unchanged({**plan['bindings'],**plan['code']})
    ctx=inspect_context(plan['original_plan'],hashes=False)
    output=exact_path(resolve(plan['output']))
    if output!=ctx['transition']/'recovery_execution_v1' or resolve(plan['durable_scope'])!=ctx['transition']:
        raise ValueError('Recovery evidence must remain within the existing1GiB reservation')
    if any(not same_identity(row,ctx['survivors'][name]) for name,row in plan['survivors'].items()) or set(plan['survivors'])!=ROLES:
        raise ValueError('Prepared survivor identities changed')
    started=time.monotonic()
    def guard():
        pss=sum(int(line.split()[1])*1024 for line in Path('/proc/self/smaps_rollup').read_text().splitlines() if line.startswith('Pss:'))
        if time.monotonic()-started>600 or pss>16*GIB or size_tree(ctx['transition'])>GIB:
            raise RuntimeError('Recovery time/memory/evidence budget exceeded')
        enforce_runtime_space(ctx['destination'],ctx['draft'])
    def precheck():
        unchanged({**plan['bindings'],**plan['code']})
        check_survivors(ctx['capture'],ctx['source'])
    def verify_parents():
        inspect_context(plan['original_plan'],hashes=True)
    def after_stop():
        deadline=time.monotonic()+10
        while True:
            try:require_parent_absent(ctx['capture'],ctx['source']);break
            except ValueError:
                if time.monotonic()>deadline:raise
                time.sleep(.1)
        unchanged({**plan['bindings'],**plan['code']})
        destination=ctx['destination']; original=ctx['original']
        prepare_lineage(ctx['source'],8000,destination/'lineage_candidate')
        return seal_handoff(ctx['source'],destination,resolve(original['capture']),destination/'lineage_candidate',owner_id=ctx['owner']['owner_id'])
    with (ctx['transition']/'controller.lock').open('r') as control, (ctx['source']/'retention.lock').open('r') as retention:
        fcntl.flock(control,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(retention,fcntl.LOCK_EX|fcntl.LOCK_NB)
        result=transaction(ctx['survivors'],output,authorized=approval['authorized'],precheck=precheck,
            verify_parents=verify_parents,after_stop=after_stop,guard=guard,
            execution_binding=dict(plan=str(plan_path),plan_sha256=sha256_file(plan_path),
                approval=str(exact_path(resolve(approval_path))),approval_sha256=sha256_file(resolve(approval_path)),
                code=plan['code'],scope=SCOPE))
    return result


if __name__=='__main__':
    def interrupted(signum,frame):raise InterruptedError('Boundary recovery interrupted: '+str(signum))
    signal.signal(signal.SIGTERM,interrupted)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--original-plan',default='runs/main_training_35k_plan_v1/authorized_transition8000_plan.json')
    p.add_argument('--plan');p.add_argument('--approval')
    mode=p.add_mutually_exclusive_group(required=True);mode.add_argument('--prepare',action='store_true');mode.add_argument('--execute',action='store_true')
    a=p.parse_args()
    if a.prepare:print(prepare(a.original_plan))
    else:
        if not a.plan or not a.approval:p.error('--execute requires --plan and --approval')
        execute(a.plan,a.approval)
