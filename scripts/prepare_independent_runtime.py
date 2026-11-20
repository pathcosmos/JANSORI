"""Prepare/publish the reviewed own-NFS runtime after actual diagnostic success.

No signals, GPU launch, observer launch, checkpoint deletion or authorization
creation. Publication consumes an exact externally supplied authorization.
"""
import fcntl
import math
import os
from pathlib import Path
import time
from types import SimpleNamespace

from common.data_contracts import resolve, sha256_file, identity
from scripts.tiered_checkpoint_retention import exact_path, atomic_json, size_tree
from scripts.independent_runtime_view import json_file
from scripts.restore_final_storage import load as storage_plan
from scripts.seal_horizon_handoff import require_parent_absent
from scripts.independent_observer_handoff import seed_history
from scripts.independent_parent_retention import CODE as PARENT_CODE, reviewed_plan
from scripts.audit_checkpoint_retention import Snapshot
from scripts.freeze_independent_prefix import ArchivedSnapshot
from scripts.extended_horizon_storage import ancestor, enforce_shared_space
from scripts.supervise_independent_training import require_absent, runtime_inputs
from train.independent_runtime_contract import REQUIRED_CODE, verify_acceptance
from train.independent_checkpoint_handoff import read_revision

OWN='scripts/prepare_independent_runtime.py'
CODE=set(REQUIRED_CODE)|set(PARENT_CODE)|{OWN,'scripts/independent_observer_handoff.py',
    'scripts/launch_independent_services.py','scripts/run_independent_followup.py'}
CAP=16*2**20


def tested_source(binding):
    """Do not borrow successful GPU evidence from a previous storage contract."""
    root=exact_path(resolve(binding['independent8gpu']['run']))
    snap=ArchivedSnapshot(root/'accepted_evidence',json_file(root/'accepted_evidence/manifest.json'))
    plan=snap.read(root/'spec.json')['plan']
    inputs={str(resolve(p)):h for p,h in plan['inputs'].items()}
    main=resolve(binding['main_run']);revision=binding['revision_template']
    if (plan['source_capture']!=binding['source_capture'] or
            inputs.get(str(main/'contract.json'))!=binding['main_contract_sha256'] or
            inputs.get(str(main/'main.json'))!=binding['main_config_sha256'] or
            resolve(plan['revision'])!=resolve(revision['path']) or
            inputs.get(str(resolve(revision['path'])))!=revision['sha256']):
        raise ValueError('Actual8GPU evidence used another source contract/revision/capture')
    snap.unchanged()


def paths(main,control):
    main,control=map(lambda p:exact_path(resolve(p)),(main,control))
    contract=json_file(main/'contract.json');pool=contract['shared_storage']['nfs_pools']['transition']
    if (len(pool['paths'])!=1 or pool['budget_bytes']!=2**30 or
            not control.is_relative_to(exact_path(pool['paths'][0])) or control==exact_path(pool['paths'][0]) or
            control.is_relative_to(main) or main.is_relative_to(control) or
            ancestor(control).stat().st_dev!=main.stat().st_dev or
            'final_storage_restoration' not in contract):
        raise ValueError('Restored final budgets and exact independent NFS control scope required')
    return main,control,contract


def check(binding,control):
    main,control,contract=paths(binding['main_run'],control)
    if (sha256_file(main/'contract.json')!=binding['main_contract_sha256'] or
            sha256_file(main/'main.json')!=binding['main_config_sha256'] or
            binding['target_steps']!=57000 or binding['target_tokens']!=119537664000 or
            binding['rank_updates']!=456000 or binding['world_size']!=8 or
            binding['nfs_keep']!=3 or binding['local_max_keep']!=15 or
            not CODE<=binding['code'].keys()):
        raise ValueError('Reviewed runtime scope/configuration/code differs')
    for name,digest in {**contract['code'],**contract['evidence'],**binding['code']}.items():
        if sha256_file(resolve(name))!=digest:raise ValueError('Runtime input changed: '+name)
    from scripts.supervise_extended_training import require_authorization
    require_authorization(contract)
    import torch
    if contract['framework']!=dict(torch=str(torch.__version__),cuda=torch.version.cuda):
        raise ValueError('Runtime framework differs from reviewed training')
    require_parent_absent(binding['source_capture'],main)
    require_absent(main,control,binding)
    elapsed=binding['elapsed_before_seconds']
    if type(elapsed) not in (int,float) or not math.isfinite(elapsed) or not 0<=elapsed<contract['max_seconds']:
        raise ValueError('Reviewed prior elapsed budget required')
    ref=binding['storage_restoration'];root=exact_path(resolve(ref['path']))
    if sha256_file(root/'report.json')!=ref['report_sha256']:
        raise ValueError('Storage restoration report changed')
    plan,restored_main,base,candidate=storage_plan(root);report=json_file(root/'report.json')
    if (restored_main!=main or candidate!=contract or
            json_file(root/'.done')!={'report_sha256':ref['report_sha256']} or
            report['status']!='restored_final_budgets_revision_prepared_gpu_pending' or
            report['contract_sha256']!=binding['main_contract_sha256'] or
            report['plan_sha256']!=sha256_file(root/'plan.json') or
            binding['source_capture']!=plan['source_capture']):
        raise ValueError('Completed storage restoration/source capture differs')
    template=binding['revision_template'];revision=exact_path(resolve(template['path']))
    if (revision!=resolve(report['revision']) or template['sha256']!=report['revision_sha256'] or
            sha256_file(revision)!=template['sha256']):
        raise ValueError('Runtime revision differs from restored boundary')
    read_revision(main,contract,revision)
    accepted=verify_acceptance(main,binding['independent8gpu'])
    tested_source(binding)
    if accepted['parent_step']!=report['step']:
        raise ValueError('Actual8GPU diagnostic used another boundary')
    view=SimpleNamespace(main=main,control=control,contract=contract,binding=binding,unchanged=lambda:None)
    seed_history(view,binding['observer_output'])
    parent=binding['parent_retirement']
    if parent['path']!=str(control/'parent_retirement_plan.json') or sha256_file(parent['path'])!=parent['sha256']:
        raise ValueError('Reviewed parent retirement plan changed')
    snap=Snapshot();reviewed_plan(view,snap);snap.unchanged()
    enforce_shared_space(main,contract)
    pool=Path(contract['shared_storage']['nfs_pools']['transition']['paths'][0])
    if size_tree(control)>CAP or size_tree(pool)+CAP>2**30:
        raise RuntimeError('Runtime publication metadata pool exhausted')
    return contract,accepted


def prepare(main,control,diagnostic,restoration,*,elapsed_before_seconds):
    main,control,contract=paths(main,control)
    if (control/'runtime_contract.json').exists() or (control/'review.json').exists():
        raise ValueError('Preserve existing runtime publication/review')
    restoration=exact_path(resolve(restoration));report=json_file(restoration/'report.json')
    plan=json_file(restoration/'plan.json');diagnostic=exact_path(resolve(diagnostic))
    if not diagnostic.is_relative_to(Path(contract['shared_storage']['nfs_pools']['transition']['paths'][0])):
        raise ValueError('Diagnostic must use the exact NFS transition pool')
    handoff=restoration/'observer_handoff/manifest.json'
    parent=control/'parent_retirement_plan.json'
    binding=dict(main_run=str(main),main_contract_sha256=sha256_file(main/'contract.json'),
        main_config_sha256=sha256_file(main/'main.json'),target_steps=57000,target_tokens=119537664000,
        rank_updates=456000,world_size=8,nfs_keep=3,local_max_keep=15,
        code={p:sha256_file(resolve(p)) for p in sorted(CODE)},
        source_capture=plan['source_capture'],elapsed_before_seconds=elapsed_before_seconds,
        revision_template=dict(path=report['revision'],sha256=report['revision_sha256']),
        storage_restoration=dict(path=str(restoration),report_sha256=sha256_file(restoration/'report.json')),
        independent8gpu=dict(run=str(diagnostic),manifest_sha256=sha256_file(diagnostic/'accepted_evidence/manifest.json'),
            verified_sha256=sha256_file(diagnostic/'accepted_evidence/verified.json')),
        observer_output=str(Path(contract['shared_storage']['nfs_pools']['observer']['paths'][0])/'independent_v1'),
        observer_handoff=dict(path=str(handoff),sha256=sha256_file(handoff)),
        parent_retirement=dict(path=str(parent),sha256=sha256_file(parent)))
    contract,accepted=check(binding,control)
    review=dict(purpose='reviewed_independent_runtime_publication_v1',binding=binding,
        accepted=accepted,prepared_unix=time.time(),publication_authorized=False,
        training_started=False,observer_started=False,checkpoints_deleted=False)
    # Parent plan is already separately reviewed here; do not manufacture it.
    atomic_json(control/'review.json',review)
    atomic_json(control/'reviewed.json',dict(review_sha256=sha256_file(control/'review.json')))
    return review


def publish(control,authorization,*,fault=lambda _:None):
    if os.environ.get('TMPDIR')!='/tmp':
        raise ValueError('Publication supervisor TMPDIR must be /tmp')
    control=exact_path(resolve(control));review=json_file(control/'review.json')
    if (json_file(control/'reviewed.json')!={'review_sha256':sha256_file(control/'review.json')} or
            review['purpose']!='reviewed_independent_runtime_publication_v1' or
            review['publication_authorized'] is not False):
        raise ValueError('Unreviewed runtime publication')
    binding=review['binding']
    auth=exact_path(resolve(authorization));approved=json_file(auth)
    if (auth!=control/'authorization.json' or approved!=dict(purpose='independent_main_runtime_authorization_v1',
            authorized=True,binding_id=identity(binding),main_run=binding['main_run'])):
        raise ValueError('Exact external runtime authorization required')
    main=exact_path(binding['main_run']);runtime=dict(purpose='independent_main_runtime_v1',binding=binding,
        authorization=dict(path=str(auth),sha256=sha256_file(auth)))
    with (main/'controller.lock').open('a') as a,(control/'publication.lock').open('a') as b:
        fcntl.flock(a,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(b,fcntl.LOCK_EX|fcntl.LOCK_NB)
        contract,accepted=check(binding,control)
        if accepted!=review['accepted']:raise ValueError('Actual acceptance changed after runtime review')
        start=review['prepared_unix']
        if (type(start) not in (int,float) or not math.isfinite(start) or not 0<start<=time.time() or
                time.time()-start+binding['elapsed_before_seconds']>=contract['max_seconds']):
            raise ValueError('Publication consumed elapsed budget or clock moved backwards')
        path=control/'runtime_contract.json';intent=control/'publication_intent.json'
        expected=dict(review_sha256=sha256_file(control/'review.json'),authorization_sha256=sha256_file(auth))
        if path.exists():
            if json_file(path)!=runtime or not intent.exists() or json_file(intent)!=expected:
                raise ValueError('Different or unowned published runtime; preserve it')
        else:
            if intent.exists() and json_file(intent)!=expected:raise ValueError('Publication intent changed')
            atomic_json(intent,expected);fault('before_runtime')
            atomic_json(path,runtime)
        fault('after_runtime')
        clock=dict(runtime_contract_sha256=sha256_file(path),started_unix=start)
        cp=control/'elapsed_clock.json'
        if cp.exists() and json_file(cp)!=clock:raise ValueError('Published runtime elapsed clock changed')
        atomic_json(cp,clock)
        # Full production entrypoint admission, without executing run()/GPU code.
        runtime_inputs(control)
        result=dict(status='published_independently_verified_runtime_not_started',
            runtime_contract_sha256=sha256_file(path),review_sha256=expected['review_sha256'],
            boundary_step=accepted['parent_step'],target_steps=57000,
            training_started=False,observer_started=False,checkpoints_deleted=False,goal_complete=False)
        atomic_json(control/'publication_report.json',result)
        return result
