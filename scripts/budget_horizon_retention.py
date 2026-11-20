"""Four explicitly bound owners share NFS latest3 / optional local oldest15.

Copy, SHA verification, ACK publication, interrupted retirement recovery and
space admission reuse the unchanged, tested journal implementation. Constructor
admission checks all previous producers are absent before creating owner state.
"""
import json

from common.data_contracts import resolve, sha256_file
from scripts.tiered_checkpoint_retention import exact_path
from scripts.horizon_retention import Branch
from scripts.extended_horizon_retention import ExtendedRetention
from scripts.seal_horizon_handoff import require_parent_absent


class BudgetRetention(ExtendedRetention):
    def __init__(self, root, contract, *, authorized=False, cancelled=None, fault=None):
        self.root=exact_path(root);self.contract=contract;self.policy=contract['tiered_retention']
        self.cancelled=cancelled or (lambda:False)
        definitions=contract['retention_branches'];self.definitions=definitions
        authorization=exact_path(resolve(contract['retention_authorization']))
        if not authorized or sha256_file(authorization)!=contract['retention_authorization_sha256']:
            raise ValueError('Missing exact four-run retention authorization')
        approval=json.loads(authorization.read_text())
        if (approval.get('authorized') is not True or approval.get('retention_transfer_authorized') is not True
                or approval.get('target_steps')!=50000 or approval.get('nfs_keep')!=3
                or approval.get('local_max_keep')!=15 or approval.get('adaptive_local_oldest_first') is not True
                or len(definitions)!=4):
            raise ValueError('Unreviewed four-run global3/adaptive15 retention')
        roots=[exact_path(resolve(d['run'])) for d in definitions]
        if (len(set(roots))!=4 or any(p.parent!=resolve('runs') for p in roots) or roots[-1]!=self.root
                or roots!=[exact_path(resolve(p)) for p in approval.get('ordered_runs', [])]):
            raise ValueError('Exact authorized four-run namespaces differ')
        boundary=approval.get('boundary_step')
        if type(boundary) is not int or not 10000<boundary<42500 or boundary%500:
            raise ValueError('Invalid 50k retention boundary')
        intervals=[(500,8000),(8500,10000),(10500,boundary),(boundary+500,50000)]
        if [(d['first_step'],d['last_step']) for d in definitions]!=intervals:
            raise ValueError('Four-run absolute intervals differ')
        self.binding_files={str(authorization):contract['retention_authorization_sha256']}
        policies=[]
        for index,(path,d) in enumerate(zip(roots,definitions)):
            policy=d['policy'];policies.append(policy)
            if (policy['nfs_keep']!=3 or policy['local_keep']!=15
                    or policy.get('adaptive_local_pruning') is not True):
                raise ValueError('Branch retention policy differs')
            if index<3:
                cp=path/'contract.json'
                if sha256_file(cp)!=d['contract_sha256'] or json.loads(cp.read_text())['tiered_retention']!=policy:
                    raise ValueError('Historical owner policy/contract changed')
                self.binding_files[str(cp)]=d['contract_sha256']
                owner=dict(version=1,run_id=policy['run_id'],nfs_run=str(path),local_root=policy['local_root'])
                if json.loads((path/'.retention_owner.json').read_text())!=owner:
                    raise ValueError('Historical NFS owner missing or changed')
            elif policy!=self.policy:
                raise ValueError('New owner policy differs')
        if len({p['run_id'] for p in policies})!=4 or len({p['local_root'] for p in policies})!=4:
            raise ValueError('Duplicate history owner or local namespace')
        spec=contract['shared_storage']
        if set(spec['local_history_paths'])!={p['local_root'] for p in policies}:
            raise ValueError('Shared history budget omits or adds an owner')
        if not any(set(pool['paths'])=={str(p) for p in roots} for pool in spec['nfs_pools'].values()):
            raise ValueError('Four runs must share one permanent retention pool')
        self.require_stopped()
        self.branches=[Branch(path,policy,authorized=True,cancelled=self.cancelled,fault=fault)
                       for path,policy in zip(roots,policies)]
        for branch in self.branches:branch.group=self
        self.local=self.branches[-1].local

    def require_stopped(self):
        for path,digest in self.binding_files.items():
            if sha256_file(exact_path(path))!=digest:
                raise ValueError('Four-run retention authorization/contract changed')
        for d in self.definitions[:3]:
            require_parent_absent(d['stopped_capture'],exact_path(resolve(d['run'])))
