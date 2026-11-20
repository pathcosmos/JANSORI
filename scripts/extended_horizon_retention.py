"""Three exact owners, one global NFS3/local15 policy for the57k extension.

Per-owner copy/SHA/ACK/retirement journals reuse the unchanged implementation.
Protected parents and diagnostic evidence are outside each training namespace.
"""
from contextlib import contextmanager,ExitStack
import json

from common.data_contracts import resolve,sha256_file
from scripts.tiered_checkpoint_retention import TieredRetention,exact_path,atomic_json,NAME
from scripts.horizon_retention import Branch,HorizonRetention
from scripts.extended_horizon_storage import enforce_shared_space
from scripts.seal_horizon_handoff import require_parent_absent


class ExtendedRetention(HorizonRetention):
    def __init__(self,root,contract,*,authorized=False,cancelled=None,fault=None):
        self.root=exact_path(root);self.contract=contract;self.policy=contract['tiered_retention']
        self.cancelled=cancelled or (lambda:False)
        definitions=contract['retention_branches'];self.definitions=definitions
        authorization=exact_path(resolve(contract['retention_authorization']))
        if not authorized or sha256_file(authorization)!=contract['retention_authorization_sha256']:
            raise ValueError('Missing exact three-run retention authorization')
        approval=json.loads(authorization.read_text())
        if (approval.get('authorized') is not True or approval.get('retention_transfer_authorized') is not True or
                approval.get('nfs_keep')!=3 or approval.get('local_max_keep')!=15 or
                approval.get('adaptive_local_oldest_first') is not True or len(definitions)!=3):
            raise ValueError('Unreviewed global3/adaptive15 retention')
        roots=[exact_path(resolve(d['run'])) for d in definitions]
        if (len(set(roots))!=3 or any(p.parent!=resolve('runs') for p in roots) or roots[-1]!=self.root or
                roots!=[resolve(approval[k]) for k in ('original_run','source_run','new_run')]):
            raise ValueError('Exact authorized three-run namespaces differ')
        boundary=approval['boundary_step']
        if type(boundary) is not int or not 8000<boundary<29750 or boundary%500:
            raise ValueError('Invalid restoration boundary')
        intervals=[(500,8000),(8500,boundary),(boundary+500,57000)]
        if [(d['first_step'],d['last_step']) for d in definitions]!=intervals:
            raise ValueError('Three-run absolute intervals differ')
        self.binding_files={str(authorization):contract['retention_authorization_sha256']}
        policies=[]
        for index,(path,d) in enumerate(zip(roots,definitions)):
            p=d['policy'];policies.append(p)
            if p['nfs_keep']!=3 or p['local_keep']!=15 or p.get('adaptive_local_pruning') is not True:
                raise ValueError('Branch retention policy differs')
            if index<2:
                cp=path/'contract.json'
                if sha256_file(cp)!=d['contract_sha256'] or json.loads(cp.read_text())['tiered_retention']!=p:
                    raise ValueError('Original owner policy/contract changed')
                self.binding_files[str(cp)]=d['contract_sha256']
                owner=dict(version=1,run_id=p['run_id'],nfs_run=str(path),local_root=p['local_root'])
                if json.loads((path/'.retention_owner.json').read_text())!=owner:
                    raise ValueError('Historical NFS ownership missing or changed')
            elif p!=self.policy:raise ValueError('New owner policy differs')
        if len({p['run_id'] for p in policies})!=3 or len({p['local_root'] for p in policies})!=3:
            raise ValueError('Duplicate history owner or local namespace')
        spec=contract['shared_storage']
        if set(spec['local_history_paths'])!={p['local_root'] for p in policies}:
            raise ValueError('Shared history budget omits or adds an owner')
        if not any(set(pool['paths'])=={str(p) for p in roots} for pool in spec['nfs_pools'].values()):
            raise ValueError('Three runs must share one permanent retention pool')
        self.branches=[Branch(path,p,authorized=True,cancelled=self.cancelled,fault=fault)
                       for path,p in zip(roots,policies)]
        for b in self.branches:b.group=self
        self.local=self.branches[-1].local

    def require_stopped(self):
        for path,digest in self.binding_files.items():
            if sha256_file(exact_path(path))!=digest:raise ValueError('Retention authorization/contract changed')
        for d in self.definitions[:2]:require_parent_absent(d['stopped_capture'],exact_path(resolve(d['run'])))

    @contextmanager
    def locked(self):
        self.require_stopped()
        with ExitStack() as stack:
            for branch in sorted(self.branches,key=lambda b:str(b.root)):stack.enter_context(branch.locked())
            self.require_stopped();yield

    def rows(self,*,local):
        result=[];pending=[];seen=set()
        for b,d in zip(self.branches,self.definitions):
            if local:rows=b.local_rows()
            else:
                rows,unverified=TieredRetention.source_rows(b)
                pending.extend(str(b.training/n) for n in unverified)
                # Partial/native failed-save residues also prevent new evictions.
                for p in b.training.glob('ckpt_*'):
                    if not NAME.fullmatch(p.name) or not (p/'.complete').is_file():pending.append(str(p))
            for name,proof in rows:
                if name in seen or not d['first_step']<=proof['step']<=d['last_step'] or proof['step']%500:
                    raise ValueError('Global history overlap or crossed branch interval')
                seen.add(name);result.append((b,name,proof))
        return sorted(result,key=lambda r:r[2]['step']),sorted(set(pending))

    def sources(self):return self.rows(local=False)
    def locals(self):return self.rows(local=True)[0]

    def budgets(self,*,startup=False,incoming_bytes=0):
        return enforce_shared_space(self.root,self.contract,local_copy_busy=True,incoming_bytes=incoming_bytes)

    def maintain(self):
        with self.locked():
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
                    if name in {n for _,n,_ in latest[-3:]}:raise ValueError('Latest3 cannot be retired')
                    b.retire(b.root,b.training/name,proof,'nfs');self.trim_local()
                self.trim_local()
            space=self.budgets();rows,pending=self.sources();local=self.locals()
            result=dict(nfs_checkpoints=[n for _,n,_ in rows],local_checkpoints=[n for _,n,_ in local],
                distinct_checkpoints=len({n for _,n,_ in rows+local}),uncertified_nfs=pending,
                nfs_paths={n:str(b.training/n) for b,n,_ in rows},
                local_paths={n:str(b.local/'checkpoints'/n) for b,n,_ in local},
                resume_scope='Certified NFS only, exact original/current/restored contract and lineage; local history is optional.',
                protected_migration_parents_separate=True,**space)
            atomic_json(self.root/'retention_state.json',result)
            atomic_json(self.root/'global_retention_state.json',result)
            return result
