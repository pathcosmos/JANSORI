"""Read-only proof of the two protected parents' authorized retirement.

The independent runtime must bind the exact plan before execution. This module
never copies/deletes checkpoints and never treats disposable archives as NFS
resume candidates. Historical copy ACKs are not fresh hashes of lost files.
"""
import hashlib
from pathlib import Path

from common.data_contracts import resolve,sha256_file
from scripts.tiered_checkpoint_retention import exact_path,records
from scripts.extended_horizon_storage import ancestor
from scripts.audit_checkpoint_retention import checkpoint_content,stat_record
from train.final_resume_probe import file_stats

CODE=['scripts/independent_parent_retention.py','scripts/archive_retire_historical_checkpoints.py',
      'scripts/copy_reviewed_checkpoint_archive.py','scripts/tiered_checkpoint_retention.py',
      'scripts/retire_independent_parents.py']


def preserved_metadata(plan,source,snap):
    """Keep original protection receipts/lock; they are not checkpoint payloads."""
    allowed={'.protect.lock',source.name+'.transaction.json',source.name+'.receipt.json'}
    found=[]
    for name,row in plan.get('preserved_nfs_metadata',{}).items():
        path=exact_path(resolve(name))
        if path.parent!=source.parent:continue
        if path.name not in allowed or not path.is_file() or path.stat().st_nlink!=1:
            raise ValueError('Unreviewed protected-parent metadata')
        before=stat_record(path);digest=sha256_file(path)
        if (before['st_size']!=row['bytes'] or digest!=row['sha256'] or before['st_size']>16*2**20 or
                (path.name=='.protect.lock' and before['st_size']!=0) or stat_record(path)!=before):
            raise ValueError('Protected-parent metadata changed')
        snap.files[str(path)]=(before,digest);found.append(path)
    return set(found)


def reviewed_plan(view,snap):
    binding=view.binding.get('parent_retirement',{})
    path=view.control/'parent_retirement_plan.json'
    if (binding.get('path')!=str(path) or binding.get('sha256')!=sha256_file(path)):
        raise ValueError('Exact runtime-bound parent retirement plan missing or changed')
    plan=snap.read(exact_path(path))
    if (plan.get('purpose')!='independent_parent_retirement_v1' or plan.get('authorized') is not True or
            plan.get('main_run')!=str(view.main) or plan.get('main_contract_sha256')!=view.binding['main_contract_sha256'] or
            plan.get('copy_workers')!=4 or plan.get('hash_workers')!=8):
        raise ValueError('Unreviewed parent retirement authorization/resources')
    for name in CODE:
        if plan.get('code',{}).get(name)!=sha256_file(resolve(name)) or view.binding['code'].get(name)!=plan['code'][name]:
            raise ValueError('Parent retirement implementation not runtime bound')
    contract=view.contract;definitions=contract['retention_branches']
    if len(definitions)!=3 or resolve(definitions[-1]['run'])!=view.main:
        raise ValueError('Three original retention owners required')
    local=exact_path(Path(contract['tiered_retention']['temp_root'])/'independent_parent_archive_v1')
    if (str(local.parent) not in contract['shared_storage']['local_temp_paths'] or
            ancestor(local).stat().st_dev==view.main.stat().st_dev):
        raise ValueError('Protected-parent archive must use exact optional local pool')
    expected=[];details=[]
    for definition,step in zip(definitions[1:],[8000,10000]):
        owner=exact_path(resolve(definition['run']));receipt=snap.read(owner/'parent_handoff.json')
        cert_path=owner/'parent_certificate.json';cert=snap.read(cert_path);files=records(cert)
        source=exact_path(owner/'parent_checkpoint'/f'ckpt_{step:07d}')
        if (resolve(receipt['parent_checkpoint'])!=source or cert['step']!=step or
                cert['status']!='passed_main_checkpoint_storage' or
                cert['training_contract_id']!=receipt['parent_metadata']['training_contract']['id']):
            raise ValueError('Protected parent identity/step differs')
        stats={name:[row[k] for k in ['st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns']]
               for name,row in receipt['parent_stats'].items()}
        if set(stats)!=set(files) or any(stats[n][2]!=files[n]['bytes'] for n in files):
            raise ValueError('Protected parent sealed inventory differs')
        slot=hashlib.sha256(str(source).encode()).hexdigest()[:24]
        entry=dict(source=str(source),destination=str(local/slot),slot=slot,source_stats=stats,bytes=cert['bytes'])
        expected.append(entry);details.append((entry,cert,receipt['parent_metadata']))
    if plan.get('entries')!=expected:raise ValueError('Exact two protected parent entries differ')
    metadata=plan.get('preserved_nfs_metadata',{})
    checked=set()
    for entry in expected:checked.update(preserved_metadata(plan,Path(entry['source']),snap))
    if checked!={exact_path(resolve(p)) for p in metadata}:
        raise ValueError('Protected metadata outside exact parent directories')
    view.unchanged();return plan,local,details


def retirement_runtime_ack(view,state,snap):
    """Recheck historical all8-consumption receipt from the accepted runtime."""
    from train.independent_runtime_contract import validate_attempt
    ack=snap.read(exact_path(state/'runtime_consumption.json'));execution=exact_path(resolve(ack['execution']))
    if (ack.get('runtime_contract_sha256')!=view.digest or execution.name!='execution.json' or
            execution.parent.parent!=view.control/'attempts'):
        raise ValueError('Retirement runtime execution scope differs')
    attempt,validated,_=validate_attempt(execution)
    if validated[0]!=view.runtime or attempt['main_run']!=str(view.main):
        raise ValueError('Retirement execution belongs to another accepted runtime')
    handle_path=execution.parent/'launch/handle.json';handle=snap.read(exact_path(handle_path))
    consumed=view.consumption(dict(path=execution,value=attempt,handle=handle))
    if consumed is None:raise ValueError('Parent retirement lacks all8-consumption ACK')
    directory=execution.parent/'consumption';supervisor=directory/'supervisor_verified.json'
    if ack.get('supervisor_ack_sha256')!=sha256_file(supervisor):
        raise ValueError('Parent retirement consumption ACK changed')
    for p in [execution,supervisor,directory/'all_ranks.json',*[directory/f'rank{rank}.json' for rank in range(8)]]:
        snap.read(exact_path(p))
    if view.consumption(dict(path=execution,value=attempt,handle=handle))!=consumed:
        raise ValueError('Parent retirement consumption changed during audit')


def verify_retired_parents(view,snap,*,hashes=True):
    if hashes is not True:raise ValueError('Final parent retirement review requires resident full SHA')
    plan,local,details=reviewed_plan(view,snap)
    children=snap.children(local);missing=not local.exists()
    if not missing and {p.name for p in children}!={e['slot'] for e,_,_ in details}:
        raise ValueError('Partial or unowned protected-parent archive residue')
    output=[]
    for entry,cert,meta in details:
        source=Path(entry['source']);dest=Path(entry['destination'])
        state=exact_path(view.control/'parent_retirement'/entry['slot'])
        tx=source.with_name('.archive-retiring-'+entry['slot'])
        remaining=snap.children(source.parent)
        if source.exists() or tx.exists() or set(remaining)!=preserved_metadata(plan,source,snap):
            raise ValueError('Protected parent remains on NFS or retirement is unfinished/unrecognized')
        if snap.read(state/'entry.json')!=entry:raise ValueError('Retired parent entry ownership differs')
        retirement_runtime_ack(view,state,snap)
        ack_path=state/'copy_ack.json';ack=snap.read(ack_path)
        expected=dict(status='verified_local_archive',source=entry['source'],destination=entry['destination'],
            files=cert['files'],bytes=entry['bytes'],source_stats=entry['source_stats'],nfs_resume_candidate=False)
        # File order is incidental; path/size/SHA coverage must match exactly.
        normalized=dict(ack);normalized['files']=sorted(ack['files'],key=lambda r:r['path'])
        expected['files']=sorted(expected['files'],key=lambda r:r['path'])
        if normalized!=expected:raise ValueError('Parent archive ACK differs from original full-state certificate')
        if sorted(snap.read(state/'verified_files.json'),key=lambda r:r['path'])!=expected['files']:
            raise ValueError('Parent archive verified file publication differs')
        intent=dict(source=str(source),transaction=str(tx),destination=str(dest),ack_sha256=sha256_file(ack_path))
        if snap.read(state/'retirement_intent.json')!=intent:raise ValueError('Parent retirement intent differs')
        retired=snap.read(state/'retired.json')
        if (retired.get('status')!='retired_nfs_verified_local_preserved' or retired.get('source')!=str(source) or
                retired.get('destination')!=str(dest) or retired.get('bytes')!=entry['bytes'] or
                retired.get('copy_ack_sha256')!=sha256_file(ack_path)):
            raise ValueError('Parent retirement completion receipt differs')
        for row in cert['files']:
            if row['path'].endswith('.distcp'):continue
            backup=exact_path(state/'source_metadata'/(hashlib.sha256(row['path'].encode()).hexdigest()+'.bin'))
            before=stat_record(backup);digest=sha256_file(backup)
            if before['st_size']!=row['bytes'] or digest!=row['sha256'] or stat_record(backup)!=before:
                raise ValueError('Durable parent metadata/RNG backup differs')
            snap.files[str(backup)]=(before,digest)
        content=None
        if not missing:
            content=checkpoint_content(dest,cert,meta['training_contract'],meta['config'],hashes=True,production=True,snapshot=snap)
            observed={n:[r[k] for k in ['st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns']] for n,r in file_stats(dest).items()}
            if observed!=retired['local_stats']:raise ValueError('Parent archive changed since verified retirement')
        output.append(dict(source=str(source),destination=str(dest),step=cert['step'],bytes=cert['bytes'],
            status='verified_retired_parent_optional_local',nfs_resume_candidate=False,
            local_namespace_missing=missing,local_content=content,full_payload_sha_refreshed=not missing,
            copy_ack_sha256=sha256_file(ack_path),retired_receipt_sha256=sha256_file(state/'retired.json')))
    snap.unchanged();view.unchanged();return output
