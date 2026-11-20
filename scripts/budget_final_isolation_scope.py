"""Derive isolation directories for reviewed50k parent and local proof paths.

Pure preparation only; does not inspect or alter permissions, assert exhaustive
dependency discovery, or authorize a diagnostic/retirement.
"""
from pathlib import Path
from common.data_contracts import resolve
from scripts.budget_final_dependency_scope import dependency_scope


def isolation_scope(contract, reviewed, local_proofs, diagnostic_root, transaction_root):
    base=dependency_scope(contract,reviewed)
    if (local_proofs.get('type')!='historical_diagnostic_training_contract_metadata_not_checkpoint_tensor_payload'
            or len(local_proofs.get('entries',[]))!=3):
        raise ValueError('Exact historical three-proof metadata inventory required')
    proof_paths=[]
    for row in local_proofs['entries']:
        p=resolve(row['original'])
        if p.name!='training_contract.json' or p.parent.name!='training':
            raise ValueError('Unexpected historical local proof path')
        proof_paths.append(p)
    if len(set(proof_paths))!=3:
        raise ValueError('Aliased local proof paths')
    dependencies=[resolve(p) for p in base['paths']]+proof_paths
    candidates=set(resolve(p) for p in base['paths'])|{p.parent for p in proof_paths}
    directories=sorted(p for p in candidates if not any(p!=q and p.is_relative_to(q) for q in candidates))
    main=resolve(contract['main_run']); own=resolve(base['own_checkpoint'])
    protected=[main,main/'training',own,resolve(diagnostic_root),resolve(transaction_root)]
    if any(any(p==d or p.is_relative_to(d) for p in protected) for d in directories):
        raise ValueError('Isolation would cover live run or diagnostic namespace')
    if any(not any(p==d or p.is_relative_to(d) for d in directories) for p in dependencies):
        raise ValueError('A dependency is outside isolation directories')
    return dict(status='derived_known_parent_and_proof_isolation_scope_only',
        dependencies=[str(p) for p in dependencies],directories=[str(p) for p in directories],
        complete_dependency_discovery=False,permissions_changed=False,access_denied_verified=False,
        actual8gpu_verified=False,retirement_authorized=False,goal_complete=False)
