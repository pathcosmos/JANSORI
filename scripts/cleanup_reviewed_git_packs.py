"""Delete only an explicitly approved, unchanged inventory of old Git garbage.

Never runs gc/prune/repack, selects new candidates, or touches training data.
Failure preserves an fsynced per-file journal; it is not automatically retried.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common.data_contracts import atomic_json,sha256_file


def validate_file(row,pack_root,minimum_age):
    p=Path(row['path'])
    if p.parent!=pack_root or not re.fullmatch(r'tmp_pack_[A-Za-z0-9]+',p.name):
        raise ValueError('Candidate outside exact temporary pack scope')
    s=p.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1:
        raise ValueError('Expected unaliased regular temporary pack')
    for key,field in [('device','st_dev'),('inode','st_ino'),('bytes','st_size'),('mtime_ns','st_mtime_ns'),('ctime_ns','st_ctime_ns')]:
        if row[key]!=getattr(s,field):raise ValueError('Candidate changed since approved inventory: '+str(p))
    if time.time()-s.st_mtime<minimum_age:raise ValueError('Candidate is too recent')
    return p


def references(root):
    names=[root/n for n in ['HEAD','index','config','packed-refs']]
    names.extend(p for directory in ['refs','logs'] for p in (root/directory).rglob('*') if p.is_file())
    return {str(p):sha256_file(p) for p in sorted(names) if p.exists()}


def no_writer(paths):
    opened=[];git=[];unreadable=[]
    for proc in Path('/proc').glob('[0-9]*'):
        comm=''
        try:
            comm=(proc/'comm').read_text().strip()
            if comm=='git' or comm.startswith('git-') or comm=='pack-objects':git.append(int(proc.name))
            for fd in (proc/'fd').iterdir():
                try:target=os.readlink(fd)
                except FileNotFoundError:continue
                if target in paths:opened.append(dict(pid=int(proc.name),fd=fd.name,path=target))
        except (FileNotFoundError,ProcessLookupError):continue
        except PermissionError:
            # Explicitly observed non-writer processes in this container.
            # Do not silently ignore any other inaccessible process.
            unreadable.append(dict(pid=int(proc.name),comm=comm))
            if comm not in ('docker-init','ssh-agent'):raise RuntimeError('Unknown inaccessible process')
    if opened or git:raise RuntimeError('Live Git command or open candidate descriptor: '+str(dict(git=git,opened=opened)))
    return unreadable


def cleanup(project,proposal_path,approval_path,output):
    project=Path(project).resolve();output=Path(output)
    plan=json.loads(Path(proposal_path).read_text());approval=json.loads(Path(approval_path).read_text())
    if (approval.get('authorized') is not True or approval['proposal_sha256']!=sha256_file(proposal_path) or
            Path(approval['proposal']).resolve()!=Path(proposal_path).resolve() or approval['candidate_count']!=len(plan['candidates'])):
        raise ValueError('Exact inventory approval missing')
    pack=project/'.git/objects/pack'
    if Path(plan['root'])!=pack or any(p.is_symlink() for p in [pack,*pack.parents]):raise ValueError('Git pack namespace differs')
    paths={r['path'] for r in plan['candidates']}
    if not paths or len(paths)!=len(plan['candidates']):raise ValueError('Empty/duplicate inventory')
    env={k:v for k,v in os.environ.items() if not k.startswith('GIT_')};env['GIT_OPTIONAL_LOCKS']='0'
    def git(*args):
        p=subprocess.run(['git','-C',str(project),'-c','gc.auto=0',*args],env=env,capture_output=True,text=True)
        if p.returncode:raise RuntimeError('Git validation failed: '+p.stderr)
        return dict(stdout=p.stdout,stderr=p.stderr,returncode=p.returncode)
    output.mkdir(parents=True,exist_ok=False)
    with (output/'cleanup.lock').open('x') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        deleted=[];before_free=shutil.disk_usage(pack).free
        try:
            if Path(git('rev-parse','--absolute-git-dir')['stdout'].strip())!=project/'.git':raise ValueError('Unexpected Git directory')
            if (project/'.git/index.lock').exists() or (project/'.git/gc.pid').exists():raise RuntimeError('Git maintenance/index lock present')
            ref=references(project/'.git');scan=no_writer(paths)
            count=git('count-objects','-v');garbage={str(project/line.split('garbage found: ',1)[1]) for line in count['stderr'].splitlines() if 'garbage found: ' in line}
            if not paths<=garbage:raise ValueError('Approved file is not currently Git garbage')
            check=git('fsck','--full','--no-reflogs')
            for row in plan['candidates']:validate_file(row,pack,plan['minimum_age_seconds'])
            atomic_json(output/'before.json',dict(references=ref,count=count,fsck=check,unreadable_nonwriters=scan,free_bytes=before_free))
            with (output/'journal.jsonl').open('x') as journal:
                def event(event,**kw):
                    journal.write(json.dumps(dict(event=event,unix=time.time(),**kw))+'\n');journal.flush();os.fsync(journal.fileno())
                for i,row in enumerate(plan['candidates']):
                    if i%10==0:
                        no_writer(paths)
                        if references(project/'.git')!=ref:raise RuntimeError('Git references/index changed; stop cleanup')
                    p=validate_file(row,pack,plan['minimum_age_seconds']);event('delete_intent',file=row)
                    p.unlink();deleted.append(str(p));event('deleted',path=str(p))
            fd=os.open(pack,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)
            after=git('fsck','--full','--no-reflogs')
            if references(project/'.git')!=ref:raise RuntimeError('Git references/index changed after cleanup')
            result=dict(status='completed_exact_approved_temporary_git_pack_cleanup',deleted_count=len(deleted),deleted_paths=deleted,
                logical_bytes=sum(r['bytes'] for r in plan['candidates']),allocated_bytes=sum(r['allocated_bytes'] for r in plan['candidates']),
                before_free_bytes=before_free,after_free_bytes=shutil.disk_usage(pack).free,fsck=after,after_count=git('count-objects','-v'),
                references_unchanged=True,scope='Only exact approved old temporary packs; no regular Git objects/checkpoints/data touched. Free-space reclamation may be delayed.')
            atomic_json(output/'report.json',result);return result
        except BaseException as exc:
            atomic_json(output/'failure.json',dict(error=str(exc),deleted_paths=deleted,automatic_retry=False));raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['project','proposal','approval','output']:p.add_argument('--'+name,required=True)
    a=p.parse_args();r=cleanup(a.project,a.proposal,a.approval,a.output)
    print(json.dumps({k:v for k,v in r.items() if k not in ('deleted_paths','fsck','after_count')}))
