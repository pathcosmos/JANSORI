"""Review/retire only four successfully sealed independent diagnostic states.

Requires a separate exact-plan authorization. Keeps NFS certificates, runtime,
comparisons, frozen lineage, archived acceptance and raw metadata/RNG backups.
Never deletes failed diagnostics, training checkpoints, data or local history.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time

from common.data_contracts import resolve,sha256_file,identity
from scripts.tiered_checkpoint_retention import exact_path,atomic_json,size_tree,fsync_dir
from scripts.seal_independent_resume_result import verify
from scripts.freeze_independent_prefix import ArchivedSnapshot
from scripts.verify_final_resume_probe import verify_inventory
from scripts.archive_retire_historical_checkpoints import no_readers
from scripts.copy_reviewed_checkpoint_archive import snapshot
from scripts.run_independent_resume import owned_processes
from scripts.extended_horizon_storage import enforce_shared_space
from train.final_resume_probe import stat_record

OUTPUTS=('restored_parent','continuous_final','split_first','resumed_final')
CODE=['scripts/retire_independent_probe_payloads.py','scripts/seal_independent_resume_result.py',
      'scripts/freeze_independent_prefix.py','scripts/verify_final_resume_probe.py',
      'scripts/archive_retire_historical_checkpoints.py','scripts/run_independent_resume.py']
CAP=48*2**20
GIB=2**30


def details(root):
    root=exact_path(resolve(root));accepted=verify(root)
    manifest=json.loads((root/'accepted_evidence/manifest.json').read_text())
    snap=ArchivedSnapshot(root/'accepted_evidence',manifest);spec=snap.read(root/'spec.json');plan=spec['plan']
    main=exact_path(resolve(plan['main_run']));contract=json.loads((main/'contract.json').read_text())
    scratch=exact_path(plan['scratch']);temp=exact_path(contract['tiered_retention']['temp_root'])
    pool=contract['shared_storage']['nfs_pools']['transition']
    if (accepted['status']!='verified_historical_independent8gpu_resume_evidence' or
            accepted['target_steps']!=57000 or root!=resolve(plan['run_dir']) or scratch!=temp/root.name or
            str(temp) not in contract['shared_storage']['local_temp_paths'] or
            len(pool['paths'])!=1 or pool['budget_bytes']!=GIB or
            not root.is_relative_to(exact_path(pool['paths'][0])) or root==exact_path(pool['paths'][0]) or
            root.stat().st_dev!=main.stat().st_dev or temp.stat().st_dev==main.stat().st_dev):
        raise ValueError('Exact completed independent diagnostic NFS/local scope required')
    certs={name:snap.read(root/'certificates'/(name+'.json')) for name in OUTPUTS}
    required={'.complete','.metadata','meta.json',*[f'rng_rank{i}.pt' for i in range(8)],*[f'__{i}_0.distcp' for i in range(8)]}
    for name,cert in certs.items():
        if resolve(cert['path'])!=scratch/name or {r['path'] for r in cert['files']}!=required:
            raise ValueError('Exact four full8rank diagnostic inventories required')
    snap.unchanged()
    return main,contract,scratch,certs,accepted


def capacity(main,contract,root):
    enforce_shared_space(main,contract)
    pool=exact_path(contract['shared_storage']['nfs_pools']['transition']['paths'][0])
    if size_tree(root/'payload_retirement')>CAP or size_tree(pool)+CAP>GIB:
        raise RuntimeError('Diagnostic retirement evidence pool unavailable')


def prepare(root):
    root=exact_path(resolve(root));main,contract,scratch,certs,accepted=details(root)
    out=root/'payload_retirement'
    if out.exists():raise ValueError('Preserve existing diagnostic retirement preparation')
    if owned_processes(root):raise ValueError('Diagnostic workers still live')
    if {p.name for p in scratch.iterdir()}!=set(OUTPUTS):raise ValueError('Unexpected local diagnostic residue')
    capacity(main,contract,root);entries=[]
    # Fresh full inventory/SHA before creating even a deletion proposal.
    for name,cert in certs.items():
        source=scratch/name;verify_inventory(source,cert,hashes=True);snapshot(source)
        entries.append(dict(source=str(source),transaction=str(scratch/('.retiring_'+name)),
            certificate_sha256=identity(cert),certificate=cert))
    out.mkdir();backups={}
    for entry in entries:
        source=Path(entry['source']);cert=entry['certificate']
        for row in cert['files']:
            if row['path'].endswith('.distcp'):continue
            path=source/row['path'];raw=path.read_bytes()
            if len(raw)>16*2**20 or hashlib.sha256(raw).hexdigest()!=row['sha256']:
                raise ValueError('Original diagnostic metadata changed or exceeds bound')
            backup=out/'metadata'/source.name/(identity(row['path'])+'.bin');backup.parent.mkdir(parents=True,exist_ok=True)
            with backup.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            backups[str(backup)]=dict(bytes=len(raw),sha256=row['sha256'])
        # The first full SHA verified these exact inode/size/mtime/ctime values.
        # Metadata copying only reads the source, so reuse it while stats match.
        verify_inventory(source,cert,hashes=False)
    for directory in sorted([p for p in out.rglob('*') if p.is_dir()],key=lambda p:len(p.parts),reverse=True):fsync_dir(directory)
    plan=dict(purpose='retire_completed_independent_probe_payloads_v1',diagnostic_run=str(root),main_run=str(main),scratch=str(scratch),
        main_contract_sha256=sha256_file(main/'contract.json'),accepted=accepted,entries=entries,metadata_backups=backups,
        code={p:sha256_file(resolve(p)) for p in CODE},
        inputs={str(p):sha256_file(p) for p in [root/'accepted_evidence/manifest.json',root/'accepted_evidence/verified.json',
            root/'frozen_prefix/manifest.json',root/'frozen_prefix/verified.json']},
        source_bytes=sum(e['certificate']['bytes'] for e in entries),metadata_cap_bytes=CAP,
        max_seconds=1800,authorized=False,requires_exact_plan_authorization=True)
    atomic_json(out/'plan.json',plan);capacity(main,contract,root)
    atomic_json(out/'prepared.json',dict(plan_sha256=sha256_file(out/'plan.json')))
    return plan


def load(root):
    root=exact_path(resolve(root));out=root/'payload_retirement';plan=json.loads((out/'plan.json').read_text())
    if (json.loads((out/'prepared.json').read_text())!={'plan_sha256':sha256_file(out/'plan.json')} or
            plan['purpose']!='retire_completed_independent_probe_payloads_v1' or plan['diagnostic_run']!=str(root) or
            plan['authorized'] is not False or plan['requires_exact_plan_authorization'] is not True or
            plan['metadata_cap_bytes']!=CAP or plan['max_seconds']!=1800):
        raise ValueError('Unreviewed diagnostic payload retirement plan')
    for p,h in {**plan['code'],**plan['inputs']}.items():
        if sha256_file(resolve(p))!=h:raise ValueError('Diagnostic cleanup binding changed')
    main,contract,scratch,certs,accepted=details(root)
    expected=[dict(source=str(scratch/name),transaction=str(scratch/('.retiring_'+name)),
        certificate_sha256=identity(cert),certificate=cert) for name,cert in certs.items()]
    if (plan['main_run']!=str(main) or plan['scratch']!=str(scratch) or plan['entries']!=expected or
            plan['accepted']!=accepted or plan['source_bytes']!=sum(e['certificate']['bytes'] for e in expected) or
            plan['main_contract_sha256']!=sha256_file(main/'contract.json')):
        raise ValueError('Archived successful diagnostic/source scope changed')
    required={str(out/'metadata'/name/(identity(r['path'])+'.bin')):dict(bytes=r['bytes'],sha256=r['sha256'])
              for name,cert in certs.items() for r in cert['files'] if not r['path'].endswith('.distcp')}
    if plan['metadata_backups']!=required:raise ValueError('Diagnostic metadata/RNG backup coverage differs')
    for p,r in required.items():
        path=exact_path(p)
        if path.stat().st_size!=r['bytes'] or sha256_file(path)!=r['sha256']:raise ValueError('Diagnostic metadata/RNG backup changed')
    return plan,main,contract,scratch


def evidence_guard(root,plan):
    """Reuse content verification only while every immutable byte source is fixed."""
    paths={resolve(p) for p in {*plan['code'],*plan['inputs'],*plan['metadata_backups']}}
    paths.update([root/'payload_retirement/plan.json',root/'payload_retirement/prepared.json',
                  resolve(plan['main_run'])/'contract.json'])
    directories={}
    for base in [root/'accepted_evidence',root/'frozen_prefix']:
        for directory in [base,*[p for p in base.rglob('*') if p.is_dir()]]:
            exact_path(directory);children=sorted(directory.iterdir())
            if any(p.is_symlink() or not (p.is_file() or p.is_dir()) for p in children):
                raise ValueError('Unexpected archived diagnostic evidence type')
            directories[directory]=[(p.name,p.is_dir()) for p in children]
            paths.update(p for p in children if p.is_file())
    before={exact_path(p):stat_record(p) for p in paths}
    def check():
        for p,stamp in before.items():
            if stat_record(exact_path(p))!=stamp:raise ValueError('Verified diagnostic evidence changed')
        for p,names in directories.items():
            if [(c.name,c.is_dir()) for c in sorted(exact_path(p).iterdir())]!=names:
                raise ValueError('Verified diagnostic evidence namespace changed')
    return check


def execute(root,authorization,*,cancel=lambda:False,fault=lambda _:None):
    root=exact_path(resolve(root));out=root/'payload_retirement';plan,main,contract,scratch=load(root)
    authorization=exact_path(resolve(authorization));approval=json.loads(authorization.read_text());digest=sha256_file(authorization)
    if (authorization!=out/'authorization.json' or approval.get('purpose')!=plan['purpose'] or
            approval.get('authorized') is not True or approval.get('plan_sha256')!=sha256_file(out/'plan.json') or
            not approval.get('user_decision')):
        raise ValueError('Separate explicit exact-plan diagnostic deletion authorization required')
    with (root/'controller.lock').open('a') as outer,(out/'execution.lock').open('a') as lock:
        fcntl.flock(outer,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        unchanged=evidence_guard(root,plan)
        # Close the content-verification/stat-capture race once; reuse it for
        # each pulse rather than recomputing the same historical24rank audit.
        if load(root)!=(plan,main,contract,scratch):raise ValueError('Diagnostic bindings changed while pinning')
        unchanged()
        started=time.monotonic();last=0.;receipts=[]
        def guard(force=False):
            nonlocal last
            if cancel():raise InterruptedError('Diagnostic cleanup cancelled; retain transaction/evidence')
            if time.monotonic()-started>1800:raise TimeoutError('Diagnostic cleanup deadline')
            if not force and time.monotonic()-last<30:return
            if owned_processes(root):raise ValueError('Diagnostic worker still live')
            if sha256_file(authorization)!=digest:raise ValueError('Deletion authorization changed')
            unchanged();capacity(main,contract,root)
            allowed=set(OUTPUTS)|{'.retiring_'+name for name in OUTPUTS}
            if scratch.exists() and any(p.name not in allowed for p in scratch.iterdir()):raise ValueError('Unknown diagnostic scratch residue')
            last=time.monotonic()
        try:
            guard(True)
            for entry in plan['entries']:
                source=Path(entry['source']);tx=Path(entry['transaction']);cert=entry['certificate']
                state=out/source.name;state.mkdir(exist_ok=True)
                binding=dict(plan_sha256=sha256_file(out/'plan.json'),authorization_sha256=digest,
                    source=str(source),transaction=str(tx),certificate_sha256=entry['certificate_sha256'])
                intent=state/'intent.json';done=state/'retired.json'
                source_verified=False
                if done.exists():
                    result=json.loads(done.read_text())
                    if result!=dict(binding,retired_bytes=cert['bytes']) or source.exists() or tx.exists():
                        raise ValueError('Completed diagnostic retirement changed')
                    receipts.append(result);continue
                if intent.exists():
                    if json.loads(intent.read_text())!=binding:raise ValueError('Diagnostic retirement intent changed')
                else:
                    if tx.exists():raise ValueError('Unowned diagnostic retirement transaction')
                    verify_inventory(source,cert,hashes=True);snapshot(source);no_readers(source)
                    source_verified=True
                    atomic_json(intent,binding)
                guard(True)
                if source.exists():
                    if tx.exists():raise ValueError('Duplicate diagnostic source/transaction')
                    verify_inventory(source,cert,hashes=not source_verified);no_readers(source)
                    source.rename(tx);fsync_dir(scratch);fault('renamed_source')
                if not tx.exists():
                    # Intent without remaining transaction may mean a crash
                    # after rmdir; require a durable per-entry removal ACK.
                    if not (state/'files_removed.json').exists():raise ValueError('Missing diagnostic state without removal proof')
                else:
                    current=snapshot(tx)
                    expected=cert['stats']
                    if set(current)-set(expected):raise ValueError('Unowned diagnostic transaction file')
                    no_readers(tx)
                    for name in sorted(current):
                        guard()
                        if stat_record(tx/name)!=expected[name]:raise ValueError('Diagnostic file changed before retirement')
                        (tx/name).unlink();fsync_dir(tx);fault('retired_file')
                    atomic_json(state/'files_removed.json',binding)
                    tx.rmdir();fsync_dir(scratch)
                if json.loads((state/'files_removed.json').read_text())!=binding:raise ValueError('Removal ACK changed')
                result=dict(binding,retired_bytes=cert['bytes']);atomic_json(done,result);receipts.append(result)
            guard(True)
            if scratch.exists():scratch.rmdir();fsync_dir(scratch.parent)
            if verify(root)!=plan['accepted']:raise ValueError('Historical acceptance changed after retirement')
            result=dict(status='retired_successful_independent_diagnostic_payloads_evidence_preserved',
                entries=receipts,retired_bytes=sum(r['retired_bytes'] for r in receipts),
                training_checkpoint_deleted=False,failed_diagnostic_deleted=False,goal_complete=False)
            atomic_json(out/'report.json',result);atomic_json(out/'.done',dict(report_sha256=sha256_file(out/'report.json')))
            return result
        except BaseException as exc:
            atomic_json(out/'failure.json',dict(error=repr(exc),elapsed_seconds=time.monotonic()-started));raise
