"""Journalled in-place directory DAC isolation of exact old resume dependencies.

No training signals, deletion of payloads, cross-filesystem moves, or automatic
production adoption. Only a separately stopped source and reviewed plan can
enter. Directory mode000 denies opens without renaming payload paths.
Payload directory children keep their exact inode/mtime/ctime throughout.
Original standalone proof files are protected through their parent directories.
No payload file is renamed, chmodded or deleted; only directory ctime changes.
"""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
from pathlib import Path
import stat

from common.data_contracts import resolve,sha256_file,identity
from scripts.tiered_checkpoint_retention import exact_path,atomic_json,fsync_dir,size_tree


def exists(path):
    return os.path.lexists(path)


def stamp(path):
    s=Path(path).lstat()
    return dict(device=s.st_dev,inode=s.st_ino,size=s.st_size,mode=stat.S_IMODE(s.st_mode),
                mtime=s.st_mtime_ns,ctime=s.st_ctime_ns,uid=s.st_uid,gid=s.st_gid)


def readable_identity(path, *, hashes):
    path=exact_path(path)
    if not exists(path):return None
    if not (path.is_dir() or path.is_file()):raise ValueError('Only regular files/directories can be isolated')
    kind='directory' if path.is_dir() else 'file'
    root=stamp(path);root.pop('ctime')  # Directory rename/chmod; file rename.
    nodes=[path] if kind=='file' else sorted(path.rglob('*'))
    files={};directories={}
    for p in nodes:
        if p.is_symlink():raise ValueError('Isolation symlink rejected')
        key='.' if p==path else str(p.relative_to(path))
        if p.is_dir():directories[key]=stamp(p)
        elif p.is_file():
            if p.stat().st_nlink!=1:raise ValueError('Hard-linked payload has another readable alias')
            row=stamp(p)
            if p==path:row.pop('ctime')
            files[key]=row
        else:raise ValueError('Special isolation payload rejected')
    def digest(item):
        key,row=item;p=path if key=='.' else path/key
        value=sha256_file(p)
        after=stamp(p)
        if key=='.':after.pop('ctime')
        if after!=row:raise ValueError('Payload changed while hashing')
        return key,value
    if hashes:
        with ThreadPoolExecutor(max_workers=8) as pool:
            values=dict(pool.map(digest,files.items()))
        files={k:dict(v,sha256=values[k]) for k,v in files.items()}
    after=stamp(path);after.pop('ctime')
    if root!=after:raise ValueError('Isolation root changed while inspecting')
    return dict(kind=kind,root=root,files=files,directories=directories,
                bytes=sum(v['size'] for v in files.values()))


def verify_readable(path, expected, *, hashes=False):
    got=readable_identity(path,hashes=hashes)
    wanted=expected
    if not hashes and expected is not None:
        wanted=dict(expected,files={k:{a:b for a,b in v.items() if a!='sha256'} for k,v in expected['files'].items()})
    if got!=wanted:raise ValueError('Isolation payload inventory/content changed')


def no_privileged_bypass():
    cap=int(next(x.split()[1] for x in Path('/proc/self/status').read_text().splitlines() if x.startswith('CapEff:')),16)
    if cap & ((1<<1)|(1<<2)):raise RuntimeError('DAC-bypass capability prevents an unavailability proof')


def require_denied(directory):
    try:fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    except PermissionError:return
    else:os.close(fd);raise ValueError('Quarantined dependency is still readable')


def source_guard(plan):
    from scripts.seal_horizon_handoff import require_parent_absent
    from scripts.run_independent_resume import owned_processes
    require_parent_absent(plan['source_capture'],resolve(plan['main_run']))
    if owned_processes(resolve(plan['diagnostic_run'])):
        raise RuntimeError('Diagnostic processes still alive; cannot change isolated paths')


def dependency_scopes(main,contract,revision):
    from train.independent_resume_probe import required_absent_paths
    dependencies=required_absent_paths(main,contract,revision)
    proof_files={str(resolve(p)) for p in revision['local_proof_backups']}
    directories=sorted({str(Path(p).parent if p in proof_files else Path(p)) for p in dependencies})
    # Merge overlapping scopes so inaccessible bytes are charged only once.
    directories=[p for p in directories if not any(Path(p).is_relative_to(Path(q)) for q in directories if p!=q)]
    return dependencies,directories


def build_plan(main,root,diagnostic,revision_path,capture_path, *, authorized=False):
    from train.independent_checkpoint_handoff import review_handoff
    main,root,diagnostic,revision_path,capture_path=map(lambda p:exact_path(resolve(p)),
        (main,root,diagnostic,revision_path,capture_path))
    if authorized is not True:raise ValueError('Explicit reversible isolation authorization required')
    if root.exists():raise ValueError('Preserve existing isolation transaction')
    c=json.loads((main/'contract.json').read_text());revision=json.loads(revision_path.read_text())
    capture=json.loads(capture_path.read_text())
    scopes=c['shared_storage']['nfs_pools']['transition']['paths']
    if (len(scopes)!=1 or root==resolve(scopes[0]) or not root.is_relative_to(resolve(scopes[0])) or
            diagnostic==root or diagnostic.is_relative_to(root) or root.is_relative_to(diagnostic)):
        raise ValueError('Exact disjoint transition transaction/diagnostic paths required')
    dependencies,paths=dependency_scopes(main,c,revision)
    plan=dict(purpose='inplace_independent_resume_dac_isolation_v1',authorized=True,main_run=str(main),
        transaction_root=str(root),diagnostic_run=str(diagnostic),revision=str(revision_path),
        source_capture=capture,source_capture_path=str(capture_path),uid=os.geteuid(),dependencies=dependencies,
        inputs={str(p):sha256_file(p) for p in [main/'contract.json',main/'main.json',main/'parent_handoff.json',
            resolve(json.loads((main/'parent_handoff.json').read_text())['source_run'])/'parent_handoff.json',
            revision_path,capture_path]},code={p:sha256_file(resolve(p)) for p in [
            'scripts/independent_resume_isolation.py','train/independent_resume_probe.py',
            'scripts/run_independent_resume.py','scripts/seal_horizon_handoff.py',
            'scripts/archive_retire_historical_checkpoints.py','scripts/tiered_checkpoint_retention.py']})
    source_guard(plan);no_privileged_bypass()
    review_handoff(main,c,json.loads((main/'main.json').read_text()),revision_path)
    entries=[]
    for name in paths:
        path=exact_path(name)
        if any(p==path or p.is_relative_to(path) for p in [main,main/'training',root,diagnostic]):
            raise ValueError('Isolation must not cover current run, own checkpoint or proof output')
        info=readable_identity(path,hashes=True)
        if info is not None and (info['kind']!='directory' or info['root']['uid']!=os.geteuid()):
            raise ValueError('Only owned dependency directories can be isolated')
        entries.append(dict(path=str(path),slot=identity(str(path))[:16],payload=info))
    plan['entries']=entries
    root.mkdir(parents=True,exist_ok=False)
    atomic_json(root/'plan.json',plan)
    atomic_json(root/'prepared.json',dict(plan_sha256=sha256_file(root/'plan.json'),plan_id=identity(plan)))
    return plan


def load_plan(root):
    root=exact_path(resolve(root));p=root/'plan.json';plan=json.loads(p.read_text())
    marker=json.loads((root/'prepared.json').read_text())
    if (marker!=dict(plan_sha256=sha256_file(p),plan_id=identity(plan)) or
            plan['purpose']!='inplace_independent_resume_dac_isolation_v1' or plan['authorized'] is not True or
            exact_path(plan['transaction_root'])!=root or plan['uid']!=os.geteuid()):
        raise ValueError('Isolation plan/identity differs')
    for p,h in {**plan['inputs'],**plan['code']}.items():
        if sha256_file(exact_path(resolve(p)))!=h:raise ValueError('Isolation input/code changed')
    if plan['source_capture']!=json.loads(Path(plan['source_capture_path']).read_text()):
        raise ValueError('Isolation process capture changed')
    main=exact_path(plan['main_run']);c=json.loads((main/'contract.json').read_text())
    revision=json.loads(Path(plan['revision']).read_text())
    deps,paths=dependency_scopes(main,c,revision)
    if plan['dependencies']!=deps or [e['path'] for e in plan['entries']]!=paths:
        raise ValueError('Isolation source coverage changed')
    for e in plan['entries']:
        exact_path(e['path'])
        if e['slot']!=identity(e['path'])[:16]:raise ValueError('Isolation slot differs')
    no_privileged_bypass()
    return root,plan


@contextmanager
def transaction(root):
    root,plan=load_plan(root)
    with (root/'transaction.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        source_guard(plan)
        yield root,plan


def root_identity(entry,uid):
    path=exact_path(entry['path']);s=stamp(path);expected=entry['payload']['root']
    if (not path.is_dir() or any(s[k]!=expected[k] for k in ['device','inode','uid','gid']) or
            s['uid']!=uid or s['mode'] not in (0,expected['mode'])):
        raise ValueError('Isolation root identity/permissions changed')
    return expected['mode']


def set_directory_mode(path,mode):
    # Mode restoration must work after a SIGKILL when no original fd survived.
    # Identity is checked by the caller before chmod. No child inode changes.
    os.chmod(path,mode,follow_symlinks=False)
    if mode:
        fsync_dir(path)
    fsync_dir(Path(path).parent)


def preserve_isolation_ack(root,entry,row):
    path=root/'isolation_receipts'/(entry['slot']+'.json')
    if path.exists():
        if json.loads(path.read_text())!=row:raise ValueError('Immutable isolation receipt changed')
    else:atomic_json(path,row)
    return path


def enter(root, *, fault=lambda _:None):
    from scripts.archive_retire_historical_checkpoints import no_readers
    with transaction(root) as (root,plan):
        if (root/'restored.json').exists():raise ValueError('Completed isolation cannot be reused')
        for e in plan['entries']:
            source_guard(plan);path=Path(e['path']);journal=root/'entries'/(e['slot']+'.json')
            if e['payload'] is None:
                if exists(path):raise ValueError('Originally absent dependency appeared')
                continue
            mode=root_identity(e,plan['uid'])
            row=json.loads(journal.read_text()) if journal.exists() else None
            if row and row['phase'] not in ('block_intent','isolated'):raise ValueError('Isolation is being restored')
            if row and row['phase']=='isolated':
                if stamp(path)!=row['blocked_stamp']:raise ValueError('Isolated root changed')
                require_denied(path);preserve_isolation_ack(root,e,row);continue
            freshly_checked=False
            if row is None:
                verify_readable(path,e['payload'],hashes=True);no_readers(path)
                freshly_checked=True
                atomic_json(journal,dict(phase='block_intent',plan_id=identity(plan),slot=e['slot']))
                fault('block_intent')
            # An unacknowledged mode000 state is unblocked and freshly hashed
            # before another attempt. Diagnostic process absence is mandatory.
            if stamp(path)['mode']==0:set_directory_mode(path,mode)
            verify_readable(path,e['payload'],hashes=not freshly_checked);no_readers(path)
            fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:os.fchmod(fd,0);os.fsync(fd)
            finally:os.close(fd)
            fsync_dir(path.parent);fault('blocked_before_ack')
            require_denied(path)
            row=dict(phase='isolated',plan_id=identity(plan),slot=e['slot'],blocked_stamp=stamp(path))
            atomic_json(journal,row);preserve_isolation_ack(root,e,row)
        proof=verify_isolated(root,require_marker=False)
        atomic_json(root/'isolated.json',proof)
        return proof


def verify_isolated(root, *, require_marker=True):
    from scripts.seal_horizon_handoff import require_parent_absent
    root,plan=load_plan(root);rows=[]
    require_parent_absent(plan['source_capture'],resolve(plan['main_run']))
    for e in plan['entries']:
        path=Path(e['path'])
        if e['payload'] is None:
            if exists(path):raise ValueError('Originally absent dependency appeared')
            continue
        p=root/'entries'/(e['slot']+'.json');row=json.loads(p.read_text())
        if (row['phase']!='isolated' or row['plan_id']!=identity(plan) or
                row['blocked_stamp']!=stamp(path) or stamp(path)['mode']!=0):
            raise ValueError('Isolation receipt/permissions/root changed')
        require_denied(path)
        receipt=root/'isolation_receipts'/(e['slot']+'.json')
        if json.loads(receipt.read_text())!=row:raise ValueError('Immutable isolation receipt differs')
        rows.append(dict(slot=e['slot'],receipt_sha256=sha256_file(receipt),blocked_stamp=row['blocked_stamp']))
    result=dict(status='verified_original_dependencies_dac_denied',plan_id=identity(plan),
        plan_sha256=sha256_file(root/'plan.json'),uid=os.geteuid(),entries=rows,
        hidden_payload_bytes=sum(e['payload']['bytes'] for e in plan['entries'] if e['payload']),
        actual8gpu_test=False,scope='Original dependency directories deny opens to this unprivileged UID. No rename, payload inode change or separate mount namespace.')
    if require_marker and json.loads((root/'isolated.json').read_text())!=result:
        raise ValueError('Isolation completion marker differs')
    if (root/'restored.json').exists():raise ValueError('Isolation already restored')
    return result


def verify_isolation_record(root):
    """Historical content proof, never an assertion of current unavailability."""
    root,plan=load_plan(root);proof=json.loads((root/'isolated.json').read_text())
    if (proof['status']!='verified_original_dependencies_dac_denied' or proof['plan_id']!=identity(plan) or
            proof['plan_sha256']!=sha256_file(root/'plan.json') or proof['uid']!=plan['uid']):
        raise ValueError('Historical isolation proof binding differs')
    wanted={e['slot']:e for e in plan['entries'] if e['payload']}
    if {r['slot'] for r in proof['entries']}!=set(wanted) or len(proof['entries'])!=len(wanted):
        raise ValueError('Historical isolation coverage differs')
    for row in proof['entries']:
        p=root/'isolation_receipts'/(row['slot']+'.json');ack=json.loads(p.read_text());e=wanted[row['slot']]
        if (sha256_file(p)!=row['receipt_sha256'] or ack!=dict(phase='isolated',plan_id=identity(plan),
                slot=row['slot'],blocked_stamp=row['blocked_stamp']) or row['blocked_stamp']['mode']!=0 or
                any(row['blocked_stamp'][k]!=e['payload']['root'][k] for k in ['device','inode','uid','gid'])):
            raise ValueError('Historical isolation receipt changed')
    if proof['hidden_payload_bytes']!=sum(e['payload']['bytes'] for e in wanted.values()):
        raise ValueError('Historical hidden-byte accounting differs')
    return dict(status='verified_historical_isolation_record',proof_sha256=sha256_file(root/'isolated.json'),
        plan_id=identity(plan),current_unavailability_verified=False,actual8gpu_verified=False)


def accounted_size(path,root):
    path=exact_path(path);verify_isolated(root);_,plan=load_plan(root)
    return size_tree(path)+sum(e['payload']['bytes'] for e in plan['entries']
        if e['payload'] and Path(e['path']).is_relative_to(path))


@contextmanager
def accounting_view(root):
    """Process-local measured usage overlay for unchanged storage validators.

    Never changes a budget or free bytes. Restores the imported function even
    when validation raises. Other server processes see no monkeypatch.
    """
    from scripts import extended_horizon_storage as storage
    proof=verify_isolated(root);_,plan=load_plan(root);previous=storage.size_tree
    if getattr(previous,'isolation_id',None)==proof['plan_id']:
        yield
        return
    if previous is not size_tree:raise ValueError('Conflicting storage accounting hook')
    blocked=[(Path(e['path']),e['payload']['bytes'],row['blocked_stamp'])
        for e in plan['entries'] if e['payload']
        for row in proof['entries'] if row['slot']==e['slot']]
    def measured(path):
        path=exact_path(path)
        if any(stamp(p)!=s for p,_,s in blocked):raise ValueError('Isolation changed during accounting')
        return previous(path)+sum(n for p,n,_ in blocked if p.is_relative_to(path))
    measured.isolation_id=proof['plan_id'];storage.size_tree=measured
    try:yield
    finally:storage.size_tree=previous


def restore(root, *, fault=lambda _:None):
    with transaction(root) as (root,plan):
        for e in reversed(plan['entries']):
            source_guard(plan);path=Path(e['path']);journal=root/'entries'/(e['slot']+'.json')
            if e['payload'] is None:
                if exists(path):raise ValueError('Originally absent dependency appeared')
                continue
            mode=root_identity(e,plan['uid'])
            row=json.loads(journal.read_text()) if journal.exists() else None
            if row and row['phase']=='isolated':
                if stamp(path)!=row['blocked_stamp']:raise ValueError('Isolated root mutated after publication')
                require_denied(path)
            elif row and row['phase'] not in ('block_intent','restore_intent','restored'):
                raise ValueError('Unexpected isolation recovery phase')
            elif row is None and stamp(path)['mode']!=mode:
                raise ValueError('Unowned permission change')
            atomic_json(journal,dict(phase='restore_intent',plan_id=identity(plan),slot=e['slot']))
            fault('restore_intent')
            set_directory_mode(path,mode);fault('unblocked')
            verify_readable(path,e['payload'],hashes=True)
            fault('restored_before_ack')
            atomic_json(journal,dict(phase='restored',plan_id=identity(plan),slot=e['slot']))
        result=dict(status='all_original_dependencies_restored',plan_id=identity(plan),
            payload_bytes=sum(e['payload']['bytes'] for e in plan['entries'] if e['payload']),
            entries=len(plan['entries']),payload_deleted=False,
            scope='Original file bytes/inodes/mtime/ctime and permissions preserved; only container directory ctime changes.')
        atomic_json(root/'restored.json',result)
        return result


