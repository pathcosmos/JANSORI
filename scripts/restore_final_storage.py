"""Restore only the four approved final pools at a stopped checkpoint boundary.

Preparation is read-only for the main run. Publication never signals processes,
starts GPUs or deletes payloads. Old contract bytes and partial transactions
are retained; an interrupted publication is resumed only by an explicit call.
"""
from copy import deepcopy
import fcntl
import hashlib
import json
from pathlib import Path

from common.data_contracts import resolve,sha256_file,identity
from scripts.tiered_checkpoint_retention import exact_path,atomic_json,fsync_dir,size_tree
from scripts.training_stage_storage_revision import DEFERRED,METADATA_ALLOWANCE,SCOPE
from scripts.extended_horizon_storage import enforce_shared_space
from train.independent_checkpoint_handoff import read_revision
from scripts.prepare_latest_independent_revision import prepare as refresh_revision,select_latest
from scripts.seal_horizon_handoff import require_parent_absent

GIB=2**30
MAX_METADATA=48*2**20
CODE=['scripts/restore_final_storage.py','scripts/prepare_latest_independent_revision.py',
      'train/independent_checkpoint_handoff.py','scripts/training_stage_storage_revision.py',
      'scripts/extended_horizon_storage.py','scripts/seal_horizon_handoff.py']


def json_file(path):return json.loads(exact_path(resolve(path)).read_text())


def candidate_contract(base,base_sha256):
    """Exact inverse budget-only revision; numerical/evidence fields preserved."""
    phase=base.get('storage_phase_revision',{})
    if (base.get('purpose')!='main_training' or base.get('target_steps')!=57000 or
            base.get('final_storage_restoration') is not None or phase.get('scope')!=SCOPE or
            phase.get('restore_before_final_stage') is not True or
            phase.get('unused_pool_metadata_cap_bytes')!=METADATA_ALLOWANCE):
        raise ValueError('Expected deferred57k final storage contract')
    authorization=exact_path(resolve(phase['authorization']))
    digest=sha256_file(authorization);approval=json_file(authorization)
    if (base['evidence'].get(str(authorization))!=digest or approval.get('authorized') is not True or
            approval.get('scope')!=SCOPE or approval.get('target_step')!=57000 or
            approval.get('restore_deferred_budget_before_final_work') is not True or
            approval.get('deferred_pool_names')!=list(DEFERRED)):
        raise ValueError('Original authorization must require final budget restoration')
    main=exact_path(resolve(base['main_run']));spec=base['shared_storage']
    if (spec['reserve_bytes']<200*GIB or spec['temporary_budget_bytes']!=1024*GIB or
            base['tiered_retention']['nfs_keep']!=3 or base['tiered_retention']['local_keep']!=15):
        raise ValueError('Original storage safety/retention must remain')
    if set(phase['deferred_pools'])!=set(DEFERRED):raise ValueError('Exact four final pools required')
    result=deepcopy(base)
    for name,gib in zip(DEFERRED,(8,1,1,100)):
        pool=phase['deferred_pools'][name];current=spec['nfs_pools'][name]
        if (pool.get('budget_bytes')!=gib*GIB or len(pool.get('paths',[]))!=1 or
                current!=dict(paths=pool['paths'],budget_bytes=METADATA_ALLOWANCE)):
            raise ValueError('Original final reservation differs: '+name)
        path=exact_path(pool['paths'][0])
        if path.exists() or path.is_relative_to(main) or main.is_relative_to(path):
            raise ValueError('Final namespace already exists or overlaps main: '+name)
        result['shared_storage']['nfs_pools'][name]=deepcopy(pool)
    result['final_storage_restoration']=dict(purpose='restore_original_final_pool_reservations_v1',
        previous_contract_sha256=base_sha256,authorization=str(authorization),authorization_sha256=digest,
        restored_pools=list(DEFERRED),additional_nfs_bytes=110*GIB-4*METADATA_ALLOWANCE,
        numerical_contract_changed=False,local_budget_changed=False,production_adoption_authorized=False)
    return result


def bound_inputs(contract):
    for name,digest in {**contract['code'],**contract['evidence']}.items():
        if sha256_file(resolve(name))!=digest:raise ValueError('Original code/evidence changed: '+name)


def capacity(main,contract):
    result=enforce_shared_space(main,contract)
    return {k:v for k,v in result.items() if k!='nfs_used_by_path'}


def prepare(main,root,template,capture,observer):
    main,root,template=map(lambda p:exact_path(resolve(p)),(main,root,template))
    if root.exists():raise ValueError('Preserve existing storage restoration preparation')
    base=json_file(main/'contract.json');base_sha=sha256_file(main/'contract.json')
    candidate=candidate_contract(base,base_sha);bound_inputs(base)
    if base['configurations']!={'main.json':sha256_file(main/'main.json')}:
        raise ValueError('Original numerical config hash differs')
    pool=base['shared_storage']['nfs_pools']['transition'];scope=exact_path(pool['paths'][0])
    if (pool['budget_bytes']!=GIB or len(pool['paths'])!=1 or root==scope or
            not root.is_relative_to(scope) or scope.stat().st_dev!=main.stat().st_dev or
            root.is_relative_to(main) or main.is_relative_to(root) or size_tree(scope)+MAX_METADATA>GIB):
        raise ValueError('Exact disjoint transition1GiB scope required')
    if (capture.get('status')!='observed_live_not_stopped' or capture.get('run')!=str(main) or
            set(capture.get('processes',{}))!={'supervisor','worker',*[f'rank{i}' for i in range(8)]}):
        raise ValueError('Exact ten producer identities required')
    args=observer['argv']
    if ('scripts/observe_extended_training_v2.py' not in args or args.count('--source-run')!=1 or
            resolve(args[args.index('--source-run')+1])!=main):
        raise ValueError('Exact existing observer capture required')
    original=read_revision(main,base,template);available=capacity(main,candidate)
    # Retain original bytes, not a reserialization, for exact hash/recovery.
    root.mkdir(parents=True,exist_ok=False)
    (root/'previous_contract.json').write_bytes((main/'contract.json').read_bytes())
    with (root/'previous_contract.json').open('rb') as f:
        import os
        os.fsync(f.fileno())
    atomic_json(root/'candidate_contract.json',candidate)
    rebased={k:deepcopy(v) for k,v in original.items() if not k.startswith('_')}
    rebased['source_contract_sha256']=sha256_file(root/'candidate_contract.json')
    rebased['storage_restoration']=dict(previous_contract_sha256=base_sha,
        candidate_contract_sha256=rebased['source_contract_sha256'],preparation=str(root))
    total=0
    for name,row in original['local_proof_backups'].items():
        source=exact_path(row['backup']);raw=source.read_bytes();total+=len(raw)
        if (not source.is_relative_to(template.parent) or source.stat().st_dev!=main.stat().st_dev or
                hashlib.sha256(raw).hexdigest()!=row['sha256'] or total>16*2**20):
            raise ValueError('Template proof backup scope/hash/size differs')
        dest=root/'proof_backups'/(identity(name)+'.json');dest.parent.mkdir(exist_ok=True)
        with dest.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        rebased['local_proof_backups'][name]=dict(backup=str(dest),sha256=row['sha256'],bytes=len(raw))
    rebased['code'].update({name:sha256_file(resolve(name)) for name in CODE})
    atomic_json(root/'rebased_template.json',rebased)
    plan=dict(purpose='stopped_boundary_final_storage_restoration_v1',main_run=str(main),root=str(root),
        base_contract_sha256=base_sha,candidate_contract_sha256=sha256_file(root/'candidate_contract.json'),
        source_capture=capture,observer_capture=observer,original_template=str(template),
        code={name:sha256_file(resolve(name)) for name in CODE},
        inputs={str(p):sha256_file(p) for p in [template,main/'main.json',main/'training/training_contract.json',
            root/'previous_contract.json',root/'candidate_contract.json',root/'rebased_template.json']},
        capacity_at_preparation=available,metadata_cap_bytes=MAX_METADATA,
        actual8gpu=False,production_adopted=False,checkpoint_deletion_authorized=False)
    plan['inputs'].update({r['backup']:r['sha256'] for r in rebased['local_proof_backups'].values()})
    atomic_json(root/'plan.json',plan)
    if sha256_file(main/'contract.json')!=base_sha or size_tree(root)>MAX_METADATA:
        raise ValueError('Source changed or preparation metadata exceeds cap')
    atomic_json(root/'prepared.json',dict(plan_sha256=sha256_file(root/'plan.json')))
    fsync_dir(root)
    return plan


def load(root):
    root=exact_path(resolve(root));plan=json_file(root/'plan.json')
    if (json_file(root/'prepared.json')!={'plan_sha256':sha256_file(root/'plan.json')} or
            plan.get('purpose')!='stopped_boundary_final_storage_restoration_v1' or plan.get('root')!=str(root)):
        raise ValueError('Unreviewed storage restoration plan')
    for name,digest in {**plan['code'],**plan['inputs']}.items():
        if sha256_file(resolve(name))!=digest:raise ValueError('Storage restoration binding changed: '+name)
    main=exact_path(plan['main_run']);base=json_file(root/'previous_contract.json');candidate=json_file(root/'candidate_contract.json')
    if (sha256_file(root/'previous_contract.json')!=plan['base_contract_sha256'] or
            sha256_file(root/'candidate_contract.json')!=plan['candidate_contract_sha256'] or
            candidate!=candidate_contract(base,plan['base_contract_sha256'])):
        raise ValueError('Only exact approved final pool restoration is permitted')
    bound_inputs(base)
    return plan,main,base,candidate


def require_quiet(plan,main,base):
    require_parent_absent(plan['source_capture'],main)
    row=plan['observer_capture'];path=Path('/proc')/str(row['pid'])/'stat'
    if path.exists() and int(path.read_text().rsplit(')',1)[1].split()[19])==row['start_ticks']:
        raise ValueError('Captured observer still present; handoff required')
    # A runtime already published against the old SHA must not be invalidated.
    scope=exact_path(base['shared_storage']['nfs_pools']['transition']['paths'][0])
    if any(scope.rglob('runtime_contract.json')):
        raise ValueError('Independent runtime already published; cannot rewrite its source')
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            args=[x.decode() for x in (proc/'cmdline').read_bytes().split(b'\0') if x]
            if '--source-run' in args:
                p=(proc/'cwd').resolve(strict=True)/args[args.index('--source-run')+1]
                if p.resolve()==main:raise ValueError('Another source consumer remains live')
        except (FileNotFoundError,ProcessLookupError):continue


def publish(root,*,fault=lambda _:None):
    """Caller stops at a certified boundary; this function never stops anything."""
    root=exact_path(resolve(root));plan,main,base,candidate=load(root)
    with (main/'controller.lock').open('a') as a,(root/'controller.lock').open('a') as b:
        fcntl.flock(a,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(b,fcntl.LOCK_EX|fcntl.LOCK_NB)
        require_quiet(plan,main,base)
        current=sha256_file(main/'contract.json')
        if current not in (plan['base_contract_sha256'],plan['candidate_contract_sha256']):
            raise ValueError('Main contract changed outside this transaction')
        intent_path=root/'publication_intent.json'
        if current==plan['candidate_contract_sha256']:
            intent=json_file(intent_path)
            if intent['plan_sha256']!=sha256_file(root/'plan.json'):
                raise ValueError('Applied contract lacks exact restoration intent')
        selected=select_latest(main)
        logged=[json.loads(line)['step'] for line in (main/'training/log.jsonl').read_text().splitlines() if line.strip()]
        if not logged or max(logged)!=selected['step'] or not 10000<selected['step']<=56500:
            raise ValueError('Stop at the exact certified boundary before budget restoration')
        available=capacity(main,candidate)
        if current==plan['base_contract_sha256']:
            if intent_path.exists():
                intent=json_file(intent_path)
                if intent['plan_sha256']!=sha256_file(root/'plan.json') or intent['step']!=selected['step']:
                    raise ValueError('Stale restoration intent')
            else:atomic_json(intent_path,dict(plan_sha256=sha256_file(root/'plan.json'),step=selected['step'],capacity=available))
            fault('before_contract')
            require_quiet(plan,main,base)
            if sha256_file(main/'contract.json')!=plan['base_contract_sha256']:raise ValueError('Main changed before publication')
            atomic_json(main/'contract.json',candidate)
        fault('after_contract')
        if sha256_file(main/'contract.json')!=plan['candidate_contract_sha256']:raise ValueError('Published contract SHA differs')
        directory=root/'latest_revision'
        if directory.exists():
            marker=json_file(directory/'prepared.json')
            if marker!=dict(revision_sha256=sha256_file(directory/'revision.json'),review_sha256=sha256_file(directory/'review.json'),production_adoption_authorized=False):
                raise ValueError('Preserve incomplete/changed restoration revision')
            from train.independent_operational_admission import PreparedOperationalAdmission
            PreparedOperationalAdmission(main,directory/'revision.json',rank=0)
        else:
            refresh_revision(main,root/'rebased_template.json',directory,
                template_sha256=sha256_file(root/'rebased_template.json'))
        fault('after_revision')
        require_quiet(plan,main,base);capacity(main,candidate)
        if sha256_file(main/'contract.json')!=plan['candidate_contract_sha256']:raise ValueError('Contract changed during revision')
        if select_latest(main)['step']!=selected['step'] or size_tree(root)>MAX_METADATA:
            raise ValueError('Boundary changed or restoration metadata cap exceeded')
        report=dict(status='restored_final_budgets_revision_prepared_gpu_pending',step=selected['step'],
            plan_sha256=sha256_file(root/'plan.json'),contract_sha256=sha256_file(main/'contract.json'),
            revision=str(directory/'revision.json'),revision_sha256=sha256_file(directory/'revision.json'),
            actual8gpu_verified=False,production_adopted=False,checkpoint_deleted=False)
        atomic_json(root/'report.json',report);atomic_json(root/'.done',dict(report_sha256=sha256_file(root/'report.json')))
        return report
