"""Explicitly approved historical NFS payload retirement after local SHA ACK.

Only the pinned inventory is managed. Main-run parents and training checkpoints
are excluded. Diagnostic metadata is copied verbatim; legacy checkpoints are
never relabelled as modern/full main-training resume candidates.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import threading
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common.data_contracts import sha256_file, identity, resolve
from scripts.copy_reviewed_checkpoint_archive import snapshot
from scripts.tiered_checkpoint_retention import exact_path, atomic_json, fsync_dir, size_tree
from scripts.extended_horizon_storage import enforce_shared_space, ancestor

GIB=2**30
MAIN_RUNS={'3b_full_v1','3b_full_35k_v1','3b_full_57k_v2'}


def validate_plan(plan):
    if (plan.get('purpose')!='approved_historical_archive_retirement_v1' or
            plan.get('authorized') is not True or plan.get('keep_local_verified_copy') is not True):
        raise ValueError('Explicit historical archive/retirement authorization required')
    root=exact_path(plan['project_root']);main=exact_path(plan['main_run'])
    review=exact_path(plan['review_path'])
    if sha256_file(review)!=plan['review_sha256']:
        raise ValueError('Reviewed historical inventory changed')
    if sha256_file(main/'contract.json')!=plan['main_contract_sha256']:
        raise ValueError('Main contract changed; stop and re-admit')
    for p,h in plan['code'].items():
        if sha256_file(root/p)!=h:raise ValueError('Archive implementation changed')
    own='scripts/archive_retire_historical_checkpoints.py'
    if plan['code'].get(own)!=sha256_file(root/own):raise ValueError('Archive implementation is not bound')
    contract=json.loads((main/'contract.json').read_text())
    allowed={str(root/r['path']):r['bytes'] for r in json.loads(review.read_text())['other_checkpoints']}
    local=exact_path(plan['local_archive_root']);evidence=exact_path(plan['execution_root'])
    if (local.parent not in [exact_path(p) for p in contract['shared_storage']['local_temp_paths']] or
            local.name!='historical_checkpoint_archive_v2' or
            not evidence.is_relative_to(root/'runs/3b_57k_transition_v2') or
            ancestor(local).stat().st_dev==main.stat().st_dev or
            ancestor(evidence).stat().st_dev!=main.stat().st_dev):
        raise ValueError('Archive scope/device differs')
    seen=set();destinations=set()
    for entry in plan['entries']:
        src=exact_path(entry['source']);dst=exact_path(entry['destination'])
        if (str(src) in seen or not src.is_relative_to(root/'runs') or
                src.relative_to(root/'runs').parts[0] in MAIN_RUNS or
                str(src) not in allowed or allowed[str(src)]!=entry['bytes']):
            raise ValueError('Protected, duplicate or unreviewed source')
        expected=hashlib.sha256(str(src).encode()).hexdigest()[:24]
        if entry['slot']!=expected or dst.name!=expected or str(dst) in destinations:
            raise ValueError('Archive source/destination slot differs')
        normal=dst.parent==local
        adopted=(dst.parent==local.with_name('historical_checkpoint_archive_v1') and entry.get('adopt_receipt'))
        if not(normal or adopted):raise ValueError('Unreviewed local destination')
        if not entry['source_stats'] or sum(v[2] for v in entry['source_stats'].values())!=entry['bytes']:
            raise ValueError('Invalid source inventory')
        for name in entry['source_stats']:
            p=Path(name)
            if p.is_absolute() or '..' in p.parts or str(p)!=name:
                raise ValueError('Unsafe relative file path')
        if not {'.complete','.metadata','meta.json'}<=set(entry['source_stats']):
            raise ValueError('Checkpoint completion/metadata missing')
        seen.add(str(src));destinations.add(str(dst))
    if not seen or plan['copy_workers']!=4 or plan['hash_workers']!=8 or plan['future_temp_bytes']!=200*GIB:
        raise ValueError('Unreviewed archive resource budget')
    return main,contract,evidence


def verify_files(root, files, *, partial=False):
    actual=snapshot(root)
    expected={r['path']:r for r in files}
    if len(expected)!=len(files) or set(actual)-expected.keys() or (not partial and set(actual)!=set(expected)):
        raise ValueError('Archive file inventory differs')
    def check(name):
        row=expected[name]
        if actual[name][2]!=row['bytes'] or sha256_file(root/name)!=row['sha256']:
            raise ValueError('Archive SHA/size differs')
    with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(check,actual))
    if snapshot(root)!=actual:raise ValueError('Archive changed during verification')
    return actual


def no_readers(source):
    """Refuse known readers/writers; inaccessible ordinary processes fail closed."""
    source=str(source);own=os.getpid()
    for proc in Path('/proc').glob('[0-9]*'):
        if int(proc.name)==own:continue
        try:
            for fd in (proc/'fd').iterdir():
                try:name=os.readlink(fd).removesuffix(' (deleted)')
                except FileNotFoundError:continue
                if name==source or name.startswith(source+'/'):
                    raise RuntimeError('Historical payload has an active file handle: '+proc.name)
        except FileNotFoundError:continue
        except PermissionError:
            # Known namespace-inaccessible infrastructure, never a data worker.
            try:comm=(proc/'comm').read_text().strip()
            except OSError:raise RuntimeError('Uninspectable process during retirement')
            if comm not in ('docker-init','ssh-agent'):
                raise RuntimeError('Uninspectable process during retirement: '+proc.name)


def archive_one(entry, state, pulse, *, fault=None):
    fault=fault or (lambda _:None)
    source=exact_path(entry['source']);dest=exact_path(entry['destination'])
    stage=dest.with_name(dest.name+'.partial');ack=state/'copy_ack.json'
    if snapshot(source)!=entry['source_stats']:
        raise ValueError('Source changed since explicit inventory')
    if stage.exists() and dest.exists():raise ValueError('Duplicate stage/final residue')
    state=exact_path(state);state.mkdir(parents=True,exist_ok=True)
    owner=state/'entry.json'
    if owner.exists():
        if json.loads(owner.read_text())!=entry:raise ValueError('Archive entry ownership changed')
    else:
        if (stage.exists() or dest.exists()) and not entry.get('adopt_receipt'):
            raise ValueError('Unowned archive destination/stage')
        atomic_json(owner,entry)
    if ack.exists():
        proof=json.loads(ack.read_text());verify_files(dest,proof['files']);return proof
    if entry.get('adopt_receipt'):
        old=exact_path(entry['adopt_receipt'])
        if sha256_file(old)!=entry['adopt_receipt_sha256']:raise ValueError('Adopted copy receipt changed')
        receipt=json.loads(old.read_text());seal=old.parent/'seal.json'
        if (receipt['source']!=str(source) or receipt['destination']!=str(dest) or
                receipt['status']!='verified_copy_source_preserved' or sha256_file(seal)!=receipt['seal_sha256']):
            raise ValueError('Adopted copy identity differs')
        sealed=json.loads(seal.read_text())
        if sealed['source_stats']!=entry['source_stats']:raise ValueError('Adopted source changed')
        files=sealed['proof']['files'];verify_files(dest,files)
    else:
        rows=state/'files';rows.mkdir(exist_ok=True)
        if dest.exists():
            seal=state/'verified_files.json'
            if not seal.exists():raise ValueError('Unowned final archive')
            files=json.loads(seal.read_text());verify_files(dest,files)
        else:
            stage.mkdir(parents=True,exist_ok=True)
            existing=snapshot(stage)
            if set(existing)-entry['source_stats'].keys():raise ValueError('Unknown partial archive residue')
            def copy(name):
                pulse()
                src=source/name;target=stage/name
                record=rows/(hashlib.sha256(name.encode()).hexdigest()+'.json')
                if record.exists():
                    r=json.loads(record.read_text())
                    if r['path']!=name:raise ValueError('Partial receipt identity differs')
                    if target.is_file() and target.stat().st_size==r['bytes'] and sha256_file(target)==r['sha256']:
                        return r
                target.parent.mkdir(parents=True,exist_ok=True)
                h=hashlib.sha256()
                with src.open('rb') as inp,target.open('wb') as out:
                    while chunk:=inp.read(8*1024**2):
                        pulse();h.update(chunk);out.write(chunk)
                    out.flush();os.fsync(out.fileno())
                s=src.stat();stamp=[s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
                if stamp!=entry['source_stats'][name]:raise ValueError('Source mutated during archive copy')
                r=dict(path=name,bytes=s.st_size,sha256=h.hexdigest())
                if sha256_file(target)!=r['sha256']:raise ValueError('Destination SHA mismatch')
                atomic_json(record,r);fault('copied_file');return r
            with ThreadPoolExecutor(max_workers=4) as pool:files=list(pool.map(copy,entry['source_stats']))
            if snapshot(source)!=entry['source_stats']:raise ValueError('Source changed during full copy')
            verify_files(stage,files)
            for directory in sorted([stage,*[p for p in stage.rglob('*') if p.is_dir()]],key=lambda p:len(p.parts),reverse=True):fsync_dir(directory)
            atomic_json(state/'verified_files.json',files)
            fault('verified_copy');stage.rename(dest);fsync_dir(dest.parent);fault('published_before_ack')
    if snapshot(source)!=entry['source_stats']:raise ValueError('Source changed before ACK')
    # Small original metadata/RNG remain durable even if disposable history is lost.
    backup=state/'source_metadata';backup.mkdir(exist_ok=True)
    for row in files:
        if not row['path'].endswith('.distcp'):
            if row['bytes']>16*1024**2:raise ValueError('Unexpectedly large metadata; review budget')
            # Avoid creating a misleading checkpoint .complete/meta directory.
            target=backup/(hashlib.sha256(row['path'].encode()).hexdigest()+'.bin')
            if not target.exists():
                with target.open('xb') as out:out.write((source/row['path']).read_bytes());out.flush();os.fsync(out.fileno())
            if sha256_file(target)!=row['sha256']:raise ValueError('Durable metadata backup differs')
    for directory in sorted([backup,*[p for p in backup.rglob('*') if p.is_dir()]],key=lambda p:len(p.parts),reverse=True):fsync_dir(directory)
    proof=dict(status='verified_local_archive',source=str(source),destination=str(dest),files=files,
               bytes=sum(r['bytes'] for r in files),source_stats=entry['source_stats'],nfs_resume_candidate=False)
    atomic_json(ack,proof);return proof


def retire_one(entry, state, pulse, *, fault=None, readers=no_readers):
    fault=fault or (lambda _:None)
    source=exact_path(entry['source']);dest=exact_path(entry['destination'])
    tx=source.with_name('.archive-retiring-'+entry['slot'])
    intent=state/'retirement_intent.json';done=state/'retired.json';ack=state/'copy_ack.json'
    if done.exists():
        if source.exists() or tx.exists():raise ValueError('Retired source unexpectedly reappeared')
        result=json.loads(done.read_text())
        if (result['source']!=str(source) or result['destination']!=str(dest) or
                result['bytes']!=entry['bytes'] or result['copy_ack_sha256']!=sha256_file(ack)):
            raise ValueError('Retirement receipt identity differs')
        verify_files(dest,json.loads(ack.read_text())['files'])
        return result
    proof=json.loads(ack.read_text())
    if (proof['source']!=str(source) or proof['destination']!=str(dest) or
            proof['source_stats']!=entry['source_stats'] or proof['bytes']!=entry['bytes']):
        raise ValueError('Copy ACK does not match retirement inventory')
    local_stats=verify_files(dest,proof['files']);pulse(force=True)
    binding=dict(source=str(source),transaction=str(tx),destination=str(dest),ack_sha256=sha256_file(ack))
    if intent.exists():
        if json.loads(intent.read_text())!=binding:raise ValueError('Retirement intent changed')
    else:
        if tx.exists():raise ValueError('Unowned retirement residue')
        if snapshot(source)!=entry['source_stats']:raise ValueError('Source changed before retirement')
        readers(source);atomic_json(intent,binding)
    if source.exists():
        if tx.exists() or snapshot(source)!=entry['source_stats']:raise ValueError('Retirement source changed')
        readers(source);source.rename(tx);fsync_dir(source.parent);fault('renamed_source')
    if tx.exists():
        actual=snapshot(tx)
        if set(actual)-entry['source_stats'].keys() or any(v!=entry['source_stats'][k] for k,v in actual.items()):
            raise ValueError('Retirement residue changed')
        readers(tx)
        for name in sorted(actual):
            pulse()
            if snapshot(dest)!=local_stats:raise ValueError('Local archive changed during retirement')
            p=tx/name;s=p.stat()
            if [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]!=entry['source_stats'][name]:
                raise ValueError('Retirement file changed')
            p.unlink();fsync_dir(p.parent);fault('retired_file')
        for directory in sorted([p for p in tx.rglob('*') if p.is_dir()],key=lambda p:len(p.parts),reverse=True):directory.rmdir()
        tx.rmdir();fsync_dir(source.parent)
    if source.exists():raise ValueError('Source still exists after retirement')
    result=dict(status='retired_nfs_verified_local_preserved',source=str(source),destination=str(dest),
                bytes=entry['bytes'],copy_ack_sha256=sha256_file(ack),local_stats=local_stats,
                utc=datetime.now(timezone.utc).isoformat())
    atomic_json(done,result);return result


def run(plan_path):
    plan_path=exact_path(plan_path);digest=sha256_file(plan_path);plan=json.loads(plan_path.read_text())
    main,contract,root=validate_plan(plan);stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    root.mkdir(parents=True,exist_ok=True)
    with (root/'execution.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        started=time.monotonic();latest=0.;mutex=threading.Lock();progress=dict(status='running',done=0,total=len(plan['entries']))
        (root/'worker.pid').write_text(str(os.getpid())+'\n')
        def pulse(force=False):
            nonlocal latest
            if stop.is_set():raise InterruptedError('Archive stopped; preserve source/copy/retirement evidence')
            if time.monotonic()-started>plan['max_seconds']:raise TimeoutError('Archive budget exhausted')
            with mutex:
                if not force and time.monotonic()-latest<30:return
                if sha256_file(plan_path)!=digest or sha256_file(main/'contract.json')!=plan['main_contract_sha256']:
                    raise ValueError('Execution/main contract changed')
                space=enforce_shared_space(main,contract)
                remaining=0
                for entry in plan['entries']:
                    dst=exact_path(entry['destination']);stage=dst.with_name(dst.name+'.partial')
                    if dst.exists() and stage.exists():raise ValueError('Duplicate copy residue')
                    resident=size_tree(dst if dst.exists() else stage)
                    if resident>entry['bytes']:raise ValueError('Copy residue exceeds planned checkpoint')
                    remaining+=entry['bytes']-resident
                if space['local_temporary_used_bytes']+remaining+plan['future_temp_bytes']+GIB>1024*GIB:
                    raise RuntimeError('Archive/final-stage peak exceeds temporary pool')
                progress.update(utc=datetime.now(timezone.utc).isoformat(),space=space,remaining_copy_bytes=remaining)
                atomic_json(root/'state.json',progress);latest=time.monotonic()
        try:
            pulse(True);results=[]
            for entry in plan['entries']:
                progress['case']=entry['source'];state=root/'entries'/entry['slot'];state.mkdir(parents=True,exist_ok=True)
                if not (state/'retirement_intent.json').exists() and not (state/'retired.json').exists():
                    archive_one(entry,state,pulse)
                result=retire_one(entry,state,pulse);results.append(result);progress['done']=len(results)
                pulse(True);print(json.dumps(result),flush=True)
            for result in results:
                if snapshot(Path(result['destination']))!=result['local_stats']:
                    raise ValueError('Verified local archive changed before batch completion')
            report=dict(status='passed',scope='Exact approved historical payloads copied/SHA-verified then retired; protected parents and main history untouched',
                        plan_sha256=digest,entries=results,total_bytes=sum(r['bytes'] for r in results),seconds=time.monotonic()-started)
            atomic_json(root/'report.json',report);atomic_json(root/'.done',dict(report_sha256=sha256_file(root/'report.json')))
            progress.update(status='complete');atomic_json(root/'state.json',progress)
        except BaseException as exc:
            progress.update(status='failed',error=repr(exc));atomic_json(root/'state.json',progress);raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--plan',required=True)
    run(parser.parse_args().plan)
