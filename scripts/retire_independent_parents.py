"""Exact two-parent archive/retirement after accepted live8rank consumption.

No training stop/start, runtime adoption, arbitrary deletion or local eviction.
Original protection receipts stay on NFS. Failed copies/retirements are retained
and can only be resumed by another explicit invocation of the same bound plan.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from common.data_contracts import resolve,sha256_file
from scripts.tiered_checkpoint_retention import exact_path,atomic_json,size_tree,records
from scripts.copy_reviewed_checkpoint_archive import snapshot
from scripts.archive_retire_historical_checkpoints import archive_one,retire_one,no_readers
from scripts.extended_horizon_storage import ancestor,enforce_shared_space
from scripts.audit_checkpoint_retention import Snapshot
from scripts.independent_runtime_view import RuntimeView
from scripts.independent_parent_retention import CODE,reviewed_plan,preserved_metadata,verify_retired_parents

GIB=2**30


def build_plan(main,control,*,authorized=False):
    """Return a reviewable plan; caller binds its SHA in the future runtime."""
    if authorized is not True:raise ValueError('Explicit parent archival/retirement authorization required')
    main,control=map(lambda p:exact_path(resolve(p)),(main,control))
    contract=json.loads((main/'contract.json').read_text());pool=contract['shared_storage']['nfs_pools']['transition']
    if (len(pool['paths'])!=1 or pool['budget_bytes']!=GIB or control==exact_path(pool['paths'][0]) or
            not control.is_relative_to(exact_path(pool['paths'][0])) or ancestor(control).stat().st_dev!=main.stat().st_dev or
            (control/'runtime_contract.json').exists()):
        raise ValueError('Plan must precede runtime publication in exact transition pool')
    local=exact_path(Path(contract['tiered_retention']['temp_root'])/'independent_parent_archive_v1')
    if str(local.parent) not in contract['shared_storage']['local_temp_paths'] or ancestor(local).stat().st_dev==main.stat().st_dev:
        raise ValueError('Exact separate local temporary pool required')
    definitions=contract['retention_branches']
    if len(definitions)!=3 or resolve(definitions[-1]['run'])!=main:raise ValueError('Three original owners required')
    entries=[];metadata={}
    for definition,step in zip(definitions[1:],[8000,10000]):
        owner=exact_path(resolve(definition['run']));receipt=json.loads((owner/'parent_handoff.json').read_text())
        cert=json.loads((owner/'parent_certificate.json').read_text());files=records(cert)
        source=exact_path(owner/'parent_checkpoint'/f'ckpt_{step:07d}')
        stats={name:[r[k] for k in ['st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns']]
               for name,r in receipt['parent_stats'].items()}
        if (snapshot(source)!=stats or set(files)!=set(stats) or cert['step']!=step or
                cert['status']!='passed_main_checkpoint_storage' or resolve(receipt['parent_checkpoint'])!=source or
                cert['training_contract_id']!=receipt['parent_metadata']['training_contract']['id'] or
                sum(r[2] for r in stats.values())!=cert['bytes']):
            raise ValueError('Exact sealed protected parent differs')
        slot=hashlib.sha256(str(source).encode()).hexdigest()[:24]
        entries.append(dict(source=str(source),destination=str(local/slot),slot=slot,source_stats=stats,bytes=cert['bytes']))
        for path in source.parent.iterdir():
            if path==source:continue
            if path.name not in ('.protect.lock',source.name+'.transaction.json',source.name+'.receipt.json') or not path.is_file():
                raise ValueError('Unrecognized parent directory residue')
            metadata[str(path)]=dict(bytes=path.stat().st_size,sha256=sha256_file(path))
    return dict(purpose='independent_parent_retirement_v1',authorized=True,main_run=str(main),
        main_contract_sha256=sha256_file(main/'contract.json'),copy_workers=4,hash_workers=8,
        code={p:sha256_file(resolve(p)) for p in CODE},entries=entries,preserved_nfs_metadata=metadata,
        future_temp_bytes=200*GIB,temporary_pool_bytes=1024*GIB,evidence_cap_bytes=48*2**20,
        max_seconds=3600,max_pss_gib=16,local_disposable=True,nfs_resume_candidate=False)


def runtime_ack(view):
    observed=view.observe()
    if (observed['phase'] in ('error','failed','paused','await_independent_execution') or
            observed['completion'] is not None or observed['consumption'] is None or
            not observed['processes'] or not observed['processes']['all8_consumed_live'] or
            observed['processes']['supervisor'] is None or observed['processes']['launcher'] is None):
        raise ValueError('Accepted live all8 independent consumption required before parent retirement')
    return dict(runtime_contract_sha256=view.digest,execution=observed['execution'],
        supervisor_ack_sha256=observed['consumption']['acknowledgement_sha256'])


def peak_space(view,plan,local):
    space=enforce_shared_space(view.main,view.contract);remaining=0
    expected={e['slot'] for e in plan['entries']}|{e['slot']+'.partial' for e in plan['entries']}
    if local.exists() and any(p.name not in expected for p in local.iterdir()):
        raise ValueError('Unowned local parent archive residue')
    for e in plan['entries']:
        dst=exact_path(e['destination']);stage=dst.with_name(dst.name+'.partial')
        if dst.exists() and stage.exists():raise ValueError('Duplicate parent copy residue')
        used=size_tree(dst if dst.exists() else stage)
        if used>e['bytes']:raise ValueError('Parent copy exceeds planned bytes')
        remaining+=e['bytes']-used
    if space['local_temporary_used_bytes']+remaining+plan['future_temp_bytes']+GIB>1024*GIB:
        raise RuntimeError('Parent archive plus final diagnostic peak exceeds1024GiB; preserve all data')
    if size_tree(view.control)>GIB or size_tree(view.control/'parent_retirement')>plan['evidence_cap_bytes']:
        raise RuntimeError('Parent retirement metadata/residue budget exceeded')
    return dict(remaining_copy_bytes=remaining,local_temporary_used_bytes=space['local_temporary_used_bytes'],
        nfs_free_bytes=space['nfs_free_bytes'],local_free_bytes=space['local_free_bytes'])


def execute(control,*,cancel=lambda:False,fault=lambda _:None):
    """RuntimeView performs actual8GPU acceptance checks before any output."""
    view=RuntimeView(control);snap=Snapshot();plan,local,details=reviewed_plan(view,snap);snap.unchanged()
    if (plan.get('future_temp_bytes')!=200*GIB or plan.get('temporary_pool_bytes')!=1024*GIB or
            plan.get('evidence_cap_bytes')!=48*2**20 or plan.get('max_seconds')!=3600 or
            plan.get('max_pss_gib')!=16 or plan.get('local_disposable') is not True or
            plan.get('nfs_resume_candidate') is not False):
        raise ValueError('Reviewed parent-retirement resource limits required')
    root=view.control/'parent_retirement'
    # Whole optional-local loss after both retirements is reportable, not a reason
    # to recreate it or treat it as an NFS resume candidate.
    if all((root/e['slot']/'retired.json').exists() for e in plan['entries']):
        return dict(status='verified_existing_parent_retirements',parents=verify_retired_parents(view,Snapshot()))
    initial=runtime_ack(view);peak_space(view,plan,local)
    root.mkdir(exist_ok=True)
    with (root/'execution.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        started=time.monotonic();last=0.;mutex=threading.Lock()
        def pulse(force=False):
            nonlocal last
            if cancel():raise InterruptedError('Parent retirement cancelled; preserve source/copy/receipts')
            if time.monotonic()-started>plan['max_seconds']:raise TimeoutError('Parent retirement time exhausted')
            with mutex:
                if not force and time.monotonic()-last<30:return
                view.unchanged()
                if runtime_ack(view)!=initial:raise ValueError('Runtime attempt/consumption changed during retirement')
                from scripts.process_accounting import proportional_tree
                if proportional_tree(os.getpid())['totals_bytes']['Pss']>plan['max_pss_gib']*GIB:
                    raise RuntimeError('Parent retirement PSS budget exceeded')
                state=peak_space(view,plan,local)
                atomic_json(root/'state.json',dict(status='running',space=state,elapsed_seconds=time.monotonic()-started))
                last=time.monotonic()
        def readers(source):pulse(True);no_readers(source)
        try:
            pulse(True)
            for entry,cert,_ in details:
                state=root/entry['slot'];source=Path(entry['source']);tx=source.with_name('.archive-retiring-'+entry['slot'])
                state.mkdir(exist_ok=True)
                ack_path=state/'runtime_consumption.json'
                if ack_path.exists():
                    if json.loads(ack_path.read_text())!=initial:
                        raise ValueError('Explicit old retirement attempt must be reviewed before new runtime resumes it')
                else:atomic_json(ack_path,initial)
                meta_snap=Snapshot();allowed=preserved_metadata(plan,source,meta_snap)|{source,tx}
                if any(p not in allowed for p in source.parent.iterdir()):raise ValueError('Unknown NFS parent residue')
                if not (state/'retirement_intent.json').exists() and not (state/'retired.json').exists():
                    archive_one(entry,state,pulse,fault=fault)
                # archive_one hashes what it copies; require that content to be
                # exactly the original certified model/optimizer/RNG inventory.
                ack=json.loads((state/'copy_ack.json').read_text())
                if sorted(ack['files'],key=lambda r:r['path'])!=sorted(cert['files'],key=lambda r:r['path']):
                    raise ValueError('Copied parent differs from original certificate')
                pulse(True);meta_snap.unchanged()
                retire_one(entry,state,pulse,fault=fault,readers=readers)
                pulse(True)
            rows=verify_retired_parents(view,Snapshot());pulse(True)
            result=dict(status='retired_two_protected_parents_verified_local_history',parents=rows,
                runtime_contract_sha256=view.digest,plan_sha256=view.binding['parent_retirement']['sha256'],
                seconds=time.monotonic()-started,goal_complete=False)
            atomic_json(root/'report.json',result);atomic_json(root/'.done',dict(report_sha256=sha256_file(root/'report.json')))
            atomic_json(root/'state.json',dict(status='complete',elapsed_seconds=time.monotonic()-started))
            return result
        except BaseException as exc:
            atomic_json(root/'state.json',dict(status='failed',error=repr(exc),elapsed_seconds=time.monotonic()-started))
            raise
