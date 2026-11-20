"""Global NFS3/local15 retention across two explicitly authorized horizon runs.

Reuse the existing per-owner copy/SHA/retirement journal protocol. Branches keep
original certificates and contract IDs; only the global selection changes.
Protected migration/diagnostic copies are separate budgeted evidence, not normal
history, and this manager never deletes those or datasets.
"""
from contextlib import contextmanager,ExitStack
import json
import math
from pathlib import Path
import shutil

from common.data_contracts import resolve,sha256_file,artifact_path
from scripts.tiered_checkpoint_retention import TieredRetention,exact_path,size_tree,atomic_json,NAME
from scripts.horizon_storage_plan import enforce_runtime_space,LocalHistoryCapacityError,GIB
from scripts.seal_horizon_handoff import require_parent_absent


class Branch(TieredRetention):
    def source_rows(self):
        rows,pending=self.group.sources()
        return [(n,p) for _,n,p in rows],pending
    def budgets(self,**kwargs):return self.group.budgets(**kwargs)
    def make_local_room(self,incoming_bytes=0):return self.group.make_local_room(incoming_bytes)


class HorizonRetention:
    def __init__(self,root,contract,*,authorized=False,cancelled=None,fault=None):
        self.root=exact_path(root);self.contract=contract;self.policy=contract['tiered_retention']
        self.cancelled=cancelled or (lambda:False)
        legacy=contract['legacy_retention'];self.source=exact_path(resolve(legacy['source_run']))
        authorization=exact_path(resolve(legacy['authorization']))
        if (not authorized or sha256_file(authorization)!=legacy['authorization_sha256'] or
                self.root==self.source or self.root.parent!=resolve('runs') or self.source.parent!=resolve('runs')):
            raise ValueError('Missing exact authorized legacy/new retention scope')
        approval=json.loads(authorization.read_text())
        if (approval.get('authorized') is not True or approval.get('retention_transfer_authorized') is not True or
                resolve(approval['source_run'])!=self.source or resolve(approval['new_run'])!=self.root or
                approval.get('nfs_keep')!=3 or approval.get('local_max_keep')!=15 or
                approval.get('adaptive_local_oldest_first') is not True):
            raise ValueError('Global3+15 retention approval differs')
        if sha256_file(self.source/'contract.json')!=legacy['source_contract_sha256']:
            raise ValueError('Original retention contract changed')
        old=json.loads((self.source/'contract.json').read_text())
        if legacy['source_policy']!=old['tiered_retention'] or self.policy['nfs_keep']!=3 or self.policy['local_keep']!=15:
            raise ValueError('Retention policies differ from exact bindings')
        self.begin=legacy['parent_step']
        if type(self.begin) is not int or self.begin<500 or self.begin%500:raise ValueError('Invalid parent retention boundary')
        self.capture=legacy['stopped_capture']
        self.branches=[Branch(self.source,legacy['source_policy'],authorized=True,cancelled=self.cancelled,fault=fault),
                       Branch(self.root,self.policy,authorized=True,cancelled=self.cancelled,fault=fault)]
        for b in self.branches:b.group=self
        self.local=self.branches[1].local
        if self.branches[0].local==self.local:raise ValueError('Legacy/new local ownership must stay distinct')

    def require_stopped(self):require_parent_absent(self.capture,self.source)

    @contextmanager
    def locked(self):
        self.require_stopped()
        with ExitStack() as stack:
            for branch in sorted(self.branches,key=lambda b:str(b.root)):stack.enter_context(branch.locked())
            self.require_stopped()
            yield

    def sources(self):
        result=[];pending=[];seen=set()
        for index,b in enumerate(self.branches):
            rows,unverified=TieredRetention.source_rows(b)
            pending.extend(str(b.training/n) for n in unverified)
            for name,proof in rows:
                if (name in seen or (index==0 and proof['step']>self.begin) or
                        (index==1 and proof['step']<=self.begin)):
                    raise ValueError('Global history overlap or crossed parent boundary')
                seen.add(name);result.append((b,name,proof))
        return sorted(result,key=lambda r:r[2]['step']),pending

    def locals(self):
        rows=[];seen=set()
        for index,b in enumerate(self.branches):
            for name,proof in b.local_rows():
                if (name in seen or (index==0 and proof['step']>self.begin) or
                        (index==1 and proof['step']<=self.begin)):
                    raise ValueError('Local history ownership/boundary differs')
                seen.add(name);rows.append((b,name,proof))
        return sorted(rows,key=lambda r:r[2]['step'])

    def budgets(self,*,startup=False,incoming_bytes=0):
        # All actual old allocations already reduce free space. Only the new
        # future reservations are added, including protected/diagnostic evidence.
        space=enforce_runtime_space(self.root,self.contract,local_copy_busy=True)
        old=self.branches[0]
        if size_tree(old.root)>old.policy['nfs_budget_bytes']:raise RuntimeError('Legacy residue budget exceeded')
        used=sum(size_tree(b.local) for b in self.branches)
        if used+incoming_bytes>self.policy['local_budget_bytes']:raise LocalHistoryCapacityError('Combined local history cap exceeded')
        free=shutil.disk_usage(self.local).free
        needed=200*GIB+max(0,1024*GIB-space['local_temporary_used_bytes'])+incoming_bytes
        if free<needed:raise LocalHistoryCapacityError('Combined local history/temp/reserve unavailable')
        return dict(nfs_used_bytes=size_tree(self.root),legacy_nfs_used_bytes=size_tree(old.root),
            local_used_bytes=used,temporary_used_bytes=space['local_temporary_used_bytes'],
            nfs_free_bytes=space['nfs_free_bytes'],local_free_bytes=free)

    def make_local_room(self,incoming_bytes=0):
        try:self.budgets(incoming_bytes=incoming_bytes);return
        except LocalHistoryCapacityError:pass
        rows,pending=self.sources()
        if pending:raise RuntimeError('Uncertified new NFS copy blocks history eviction')
        nfs={n for _,n,_ in rows}
        for b,name,proof in self.locals():
            if name in nfs:continue
            b.retire(b.local,b.local/'checkpoints'/name,proof,'local')
            try:self.budgets(incoming_bytes=incoming_bytes);return
            except LocalHistoryCapacityError:pass
        raise LocalHistoryCapacityError('No eligible global local history can satisfy reserve; preserve other artifacts')

    def trim_local(self):
        rows,pending=self.sources()
        if pending:return
        nfs={n for _,n,_ in rows}
        unique=[r for r in self.locals() if r[1] not in nfs]
        for b,name,proof in unique[:-15]:b.retire(b.local,b.local/'checkpoints'/name,proof,'local')

    def maintain(self):
        with self.locked():
            # Resume exact existing owner journals before selecting new work.
            for b in self.branches:
                for base in (b.root,b.local):
                    for tx in sorted((base/'retention_trash').iterdir()):
                        if not NAME.fullmatch(tx.name) or tx.is_symlink():raise ValueError('Unknown retirement residue')
                        b.finish_retirement(base,tx)
            rows,pending=self.sources()
            if not pending:
                self.make_local_room()
                for b,name,proof in rows[:-3]:
                    b.publish_copy(name,proof)
                    latest,unverified=self.sources()
                    if unverified:break
                    if name in {n for _,n,_ in latest[-3:]}:raise ValueError('Global latest3 cannot be retired')
                    b.retire(b.root,b.training/name,proof,'nfs')
                    self.trim_local()
                self.trim_local()
            self.budgets()
            rows,pending=self.sources();local=self.locals()
            result=dict(nfs_checkpoints=[n for _,n,_ in rows],local_checkpoints=[n for _,n,_ in local],
                distinct_checkpoints=len({n for _,n,_ in rows+local}),uncertified_nfs=pending,
                nfs_paths={n:str(b.training/n) for b,n,_ in rows},
                local_paths={n:str(b.local/'checkpoints'/n) for b,n,_ in local},
                resume_scope='New NFS checkpoints use the35k contract; retained old NFS checkpoints use their original contract. Local history is never a resume source.',
                protected_migration_parent_separate=True,**self.budgets())
            atomic_json(self.root/'retention_state.json',result)
            atomic_json(self.root/'global_retention_state.json',result)
            return result
