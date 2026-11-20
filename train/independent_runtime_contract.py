"""Fail-closed runtime authorization and all-rank consumption contracts.

No launch, signals, pruning or self-authorization. The future versioned
supervisor must hold the original controller lock and publish an exact attempt.
"""
import json
import os
from pathlib import Path
import re

from common.data_contracts import resolve,sha256_file,identity
from scripts.tiered_checkpoint_retention import exact_path
from train.independent_resume_bridge import BRIDGE_FILES

MODULE='train.monitored_independent_horizon'
SUPERVISOR='scripts/supervise_independent_training.py'
REQUIRED_CODE={*BRIDGE_FILES,'train/independent_operational_admission.py',
    'train/independent_runtime_contract.py','train/monitored_independent_horizon.py',
    'scripts/prepare_latest_independent_revision.py','scripts/seal_independent_resume_result.py',
    'scripts/freeze_independent_prefix.py','scripts/horizon_probe_process.py',SUPERVISOR,
    'scripts/independent_runtime_view.py','scripts/observe_independent_training.py'}


def verify_acceptance(main,reference):
    from scripts.seal_independent_resume_result import verify
    from scripts.freeze_independent_prefix import ArchivedSnapshot
    root=exact_path(resolve(reference['run']))
    if (sha256_file(root/'accepted_evidence/manifest.json')!=reference['manifest_sha256'] or
            sha256_file(root/'accepted_evidence/verified.json')!=reference['verified_sha256']):
        raise ValueError('Independent8GPU acceptance pin differs')
    accepted=verify(root)
    manifest=json.loads((root/'accepted_evidence/manifest.json').read_text())
    snap=ArchivedSnapshot(root/'accepted_evidence',manifest)
    spec=snap.read(root/'spec.json');plan=spec['plan']
    current=json.loads((main/'training/training_contract.json').read_text())
    if (accepted['status']!='verified_historical_independent8gpu_resume_evidence' or
            resolve(accepted['source_run'])!=main or accepted['target_steps']!=57000 or
            accepted['parent_training_contract_id']!=current['id'] or
            plan.get('shared_admission_required') is not True or
            plan.get('operational_consumption_required') is not True or
            plan.get('frozen_prefix_required') is not True):
        raise ValueError('Independent8GPU acceptance scope/gates differ')
    # Actual GPU evidence cannot be borrowed after changing the tested hooks.
    for name in {*BRIDGE_FILES,'train/independent_operational_admission.py','train/independent_resume_probe.py'}:
        if plan['code'].get(name)!=sha256_file(resolve(name)):
            raise ValueError('Actual independent8GPU tested implementation differs: '+name)
    snap.unchanged()
    return accepted


def validate_contract(path):
    path=exact_path(resolve(path));runtime=json.loads(path.read_text());binding=runtime['binding']
    main=exact_path(resolve(binding['main_run']));control=path.parent
    if (runtime.get('purpose')!='independent_main_runtime_v1' or
            binding.get('target_steps')!=57000 or binding.get('target_tokens')!=119537664000 or
            binding.get('rank_updates')!=456000 or binding.get('world_size')!=8 or
            binding.get('nfs_keep')!=3 or binding.get('local_max_keep')!=15 or
            main.parent!=resolve('runs') or control.is_relative_to(main) or main.is_relative_to(control)):
        raise ValueError('Unreviewed independent runtime scope')
    contract=json.loads((main/'contract.json').read_text())
    pool=contract['shared_storage']['nfs_pools']['transition']
    if (pool['budget_bytes']!=2**30 or len(pool['paths'])!=1 or
            not control.is_relative_to(exact_path(pool['paths'][0])) or
            control.stat().st_dev!=main.stat().st_dev or path.name!='runtime_contract.json'):
        raise ValueError('Runtime control must remain in exact NFS transition pool')
    for p,key in [(main/'contract.json','main_contract_sha256'),(main/'main.json','main_config_sha256')]:
        if sha256_file(p)!=binding[key]:raise ValueError('Original main runtime contract changed')
    from scripts.supervise_extended_training import require_authorization
    require_authorization(contract)
    import torch
    if contract['framework']!=dict(torch=str(torch.__version__),cuda=torch.version.cuda):
        raise ValueError('Runtime framework differs from original validation')
    code=binding['code']
    if not REQUIRED_CODE<=set(code):raise ValueError('Independent runtime implementation coverage missing')
    for bindings in (contract['code'],contract['evidence'],code):
        for p,h in bindings.items():
            if sha256_file(resolve(p))!=h:raise ValueError('Runtime implementation/input changed: '+p)
    auth=exact_path(resolve(runtime['authorization']['path']))
    if (not auth.is_relative_to(control) or sha256_file(auth)!=runtime['authorization']['sha256']):
        raise ValueError('Exact runtime authorization pin required')
    approved=json.loads(auth.read_text())
    if approved!=dict(purpose='independent_main_runtime_authorization_v1',authorized=True,
            binding_id=identity(binding),main_run=str(main)):
        raise ValueError('Exact reviewed runtime execution authorization required')
    diagnostic=exact_path(resolve(binding['independent8gpu']['run']))
    if (not diagnostic.is_relative_to(exact_path(pool['paths'][0])) or
            diagnostic.stat().st_dev!=main.stat().st_dev):
        raise ValueError('Runtime acceptance must remain in exact NFS transition pool')
    accepted=verify_acceptance(main,binding['independent8gpu'])
    return runtime,contract,accepted


def validate_attempt(path):
    path=exact_path(resolve(path));attempt=json.loads(path.read_text())
    runtime_path=exact_path(resolve(attempt['runtime_contract']))
    if sha256_file(runtime_path)!=attempt['runtime_contract_sha256']:
        raise ValueError('Attempt runtime contract pin differs')
    validated=validate_contract(runtime_path)
    rc,main_contract,accepted=validated;control=runtime_path.parent
    name=attempt['attempt'];main=exact_path(resolve(rc['binding']['main_run']))
    if (not re.fullmatch('independent-[0-9a-f]{32}',name) or
            path!=control/'attempts'/name/'execution.json' or
            attempt.get('purpose')!='independent_main_attempt_v1' or
            attempt.get('world_size')!=8 or attempt.get('main_run')!=str(main) or
            attempt['training_contract_id']!=accepted['parent_training_contract_id']):
        raise ValueError('Exact independent attempt identity/path required')
    revision=exact_path(resolve(attempt['revision']))
    if sha256_file(revision)!=attempt['revision_sha256']:
        raise ValueError('Attempt revision pin differs')
    shared=exact_path(resolve(attempt['shared_admission']['path']))
    if shared!=path.parent/'shared_admission.json' or sha256_file(shared)!=attempt['shared_admission']['sha256']:
        raise ValueError('Attempt shared admission pin differs')
    row=json.loads(shared.read_text());bound=dict(purpose='independent_main_attempt',
        runtime_contract_sha256=attempt['runtime_contract_sha256'],attempt=name)
    if (row['binding']!=bound or row['production_adoption_authorized'] is not False or
            row['purpose']!='shared_own_nfs_admission_v1' or resolve(row['root'])!=main or
            resolve(row['revision_path'])!=revision or
            row['candidate']['id']!=attempt['training_contract_id'] or
            not accepted['parent_step']<=row['child_proof']['step']<57000):
        raise ValueError('Attempt shared source/accepted horizon differs')
    return attempt,validated,bound


def launcher_identity(execution,attempt):
    """Check live parent torchrun, its owned-command receipt and supervisor."""
    from scripts.horizon_probe_process import OWNER_ENV
    from scripts.horizon_process_handoff import process_record,same_identity
    parent=process_record(os.getppid());execution=exact_path(resolve(execution))
    handle=json.loads((execution.parent/'launch/handle.json').read_text())
    expected=['--module',MODULE,'--execution',str(execution)]
    args=parent['argv']
    if ('torch.distributed.run' not in args or args[-len(expected):]!=expected or
            '--nproc_per_node=8' not in args or parent['pid']!=handle['pid'] or
            parent['start_ticks']!=handle['process']['start_ticks'] or
            os.environ.get(OWNER_ENV)!=handle['owner'] or not re.fullmatch('[0-9a-f]{32}',handle['owner'])):
        raise ValueError('Live owned8rank launcher identity differs')
    supervisor=process_record(parent['ppid']);wanted=attempt['supervisor']
    if (not same_identity(wanted,supervisor) or
            str(resolve(SUPERVISOR)) not in [str((Path(supervisor['cwd'])/a).resolve()) for a in supervisor['argv']] or
            '--control-dir' not in supervisor['argv']):
        raise ValueError('Live runtime supervisor identity differs')
    i=supervisor['argv'].index('--control-dir')
    if resolve(supervisor['argv'][i+1])!=Path(attempt['runtime_contract']).parent:
        raise ValueError('Runtime supervisor control namespace differs')
    return dict(launcher_pid=parent['pid'],launcher_start_ticks=parent['start_ticks'],owner=handle['owner'])


def verify_consumption_rows(attempt,rows,*,owner):
    """Pure exact coverage/binding gate; supervisor also verifies live PIDs."""
    if len(rows)!=8 or {r['rank'] for r in rows}!=set(range(8)):
        raise ValueError('All8rank consumption receipts required')
    shared=json.loads(resolve(attempt['shared_admission']['path']).read_text())
    if sha256_file(resolve(attempt['shared_admission']['path']))!=attempt['shared_admission']['sha256']:
        raise ValueError('Consumed shared admission changed')
    identities=set();devices=set();pids=set()
    for r in rows:
        value=r['consumption']
        expected=dict(status='rank_own_nfs_state_consumed',source_run=attempt['main_run'],rank=r['rank'],
            checkpoint=shared['child'],step=shared['child_proof']['step'],training_contract_id=attempt['training_contract_id'],
            checkpoint_certificate_id=identity(shared['child_proof']),pid=r['pid'],start_ticks=r['start_ticks'],
            model_optimizer_load_completed=True,cpu_cuda_rng_applied=True,
            source_may_rotate_after_all8rank_consumption=True,actual8gpu_acceptance=False)
        if (type(r['rank']) is not int or type(r['pid']) is not int or type(r['start_ticks']) is not int or
                r['attempt']!=attempt['attempt'] or r['owner']!=owner or
                r['runtime_contract_sha256']!=attempt['runtime_contract_sha256'] or
                r['shared_admission_sha256']!=attempt['shared_admission']['sha256'] or
                r['backend']!='nccl' or r['world_size']!=8 or r['local_rank']!=r['rank'] or
                value!=expected or not r['device_uuid'] or r['pid']<=1 or r['start_ticks']<=0):
            raise ValueError('Rank consumption execution binding differs')
        identities.add((r['pid'],r['start_ticks']));devices.add(r['device_uuid']);pids.add(r['pid'])
    if len(identities)!=8 or len(devices)!=8 or len(pids)!=8:raise ValueError('Distinct8rank processes/devices required')
    return dict(status='all8_own_state_consumed',attempt=attempt['attempt'],
        shared_admission_sha256=attempt['shared_admission']['sha256'],ranks=8,
        checkpoint=shared['child'],step=shared['child_proof']['step'])
