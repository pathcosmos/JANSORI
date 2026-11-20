"""Read-only global3/local15 content audit across four exact50k lineage owners.

No manager construction, local repair, checkpoint copy or retirement. Complete
optional local namespace loss is reported separately from permanent resume.
"""
from contextlib import ExitStack
from datetime import datetime,timezone
import fcntl
from pathlib import Path
from common.data_contracts import resolve,artifact_path,sha256_file
from scripts.audit_checkpoint_retention import (Snapshot,ChangedDuringAudit,checkpoint_content,
    journal_review,existing_ancestor,NAME,GIB)
from scripts.tiered_checkpoint_retention import inventory,records,size_tree,exact_path
from train.final_resume_probe import file_stats

def branch_inventory(root, policy, parent, config, *, first_step, last_step, hashes, production, snapshot):
    root = exact_path(resolve(root)); local = exact_path(Path(policy['local_root'])); snap = snapshot
    owner = dict(version=1,run_id=policy['run_id'],nfs_run=str(root),local_root=str(local))
    from scripts.tiered_checkpoint_retention import reviewed_policy
    if (policy['nfs_run'] != str(root) or not reviewed_policy(policy) or
            policy['resume_from_local'] is not False or local.is_relative_to(root) or root.is_relative_to(local)):
        raise ValueError('Unreviewed10/15 namespace/resume policy')
    if snap.read(root/'.retention_owner.json') != owner:
        raise ValueError('NFS owner receipt differs')
    certs = {}; cert_digests = {}
    for p in snap.children(root/'checkpoint_certificates'):
        name = p.stem
        if p.suffix != '.json' or not NAME.fullmatch(name) or not p.is_file():
            raise ValueError('Unexpected checkpoint certificate entry')
        proof = snap.read(p)
        if (proof['status'] != 'passed_main_checkpoint_storage' or proof['step'] != int(name[5:]) or
                proof['training_contract_id'] != parent['id'] or
                proof['checkpoint'] != policy['artifact_training_prefix']+'/'+name):
            raise ValueError('Checkpoint certificate identity differs')
        records(proof); certs[name] = proof; cert_digests[p.name] = snap.files[str(p)][1]
    if config['ckpt_every']!=500 or first_step%500 or last_step%500 or first_step>last_step+500:
        raise ValueError('Unreviewed branch interval')
    expected_steps = set(range(first_step,last_step+1,500))
    actual_steps = {p['step'] for p in certs.values()}
    if actual_steps != expected_steps:
        raise ValueError('Checkpoint certificate schedule coverage differs')
    events = snap.read(root/'retention_journal.jsonl',lines=True)
    journal = journal_review(events,policy['run_id'],certs,cert_digests)
    nfs = {}; archived = {}; residues = []; overlap = []
    def residue(path,why):
        residues.append(dict(path=artifact_path(path),reason=why,bytes=size_tree(path) if path.is_dir() else path.stat().st_size))
    for p in snap.children(root/'training'):
        if p.name in ('training_contract.json','log.jsonl'):
            continue
        if not p.is_dir() or not NAME.fullmatch(p.name) or not (p/'.complete').is_file() or p.name not in certs:
            residue(p,'incomplete_unverified_or_unexpected_nfs_entry'); continue
        nfs[p.name] = checkpoint_content(p,certs[p.name],parent,config,hashes=hashes,production=production,snapshot=snap)
    local_children = snap.children(local)
    local_missing = not local.exists()
    if not local_missing:
        if root.stat().st_dev == local.stat().st_dev or snap.read(local/'.retention_owner.json') != owner:
            raise ValueError('Local owner/device differs')
        allowed = {'.retention_owner.json','checkpoints','certificates','staging','retention_trash'}
        for p in local_children:
            if p.name not in allowed:
                residue(p,'unexpected_local_namespace_entry')
        if any(not (local/name).is_dir() for name in allowed-{'.retention_owner.json'}):
            raise ValueError('Partially missing local namespace')
        for p in snap.children(local/'checkpoints'):
            receipt = local/'certificates'/(p.name+'.json')
            if not p.is_dir() or not NAME.fullmatch(p.name) or p.name not in certs or not receipt.is_file() or not (p/'.complete').is_file():
                residue(p,'incomplete_unverified_local_copy'); continue
            if snap.read(receipt) != certs[p.name]:
                raise ValueError('Local ACK differs from durable NFS certificate')
            archived[p.name] = checkpoint_content(p,certs[p.name],parent,config,hashes=hashes,production=production,snapshot=snap)
        for p in snap.children(local/'certificates'):
            if p.name not in {name+'.json' for name in archived}:
                residue(p,'orphan_or_unverified_local_certificate')
        for name in ['staging','retention_trash']:
            for p in snap.children(local/name):
                residue(p,'local_'+name+'_residue')
    for p in snap.children(root/'retention_trash'):
        residue(p,'nfs_retirement_residue')
    return dict(root=str(root),local_root=str(local),nfs=nfs,local=archived,
        certificates=certs,certificate_receipts=cert_digests,journal=journal,residues=residues,
        local_namespace_missing=local_missing,training_contract_id=parent['id'])


def inventory_group(branches, *, through_step, hashes, final, production=True, snapshot=None):
    from scripts.budget_final_lineage import intervals
    if len(branches)!=4 or (final and not hashes) or (production and final and through_step!=50000):
        raise ValueError('Exact four-branch final full-SHA scope required')
    snap=snapshot or Snapshot()
    original,current,previous,new=branches
    boundary=previous['last_step']
    expected=intervals(boundary,checkpoints=True)
    expected[-1]=(boundary+500,through_step)
    if ([(b['first_step'],b['last_step']) for b in branches]!=expected or through_step<boundary
            or len({str(b['root']) for b in branches})!=4
            or len({b['parent']['id'] for b in branches})!=4
            or len({b['policy']['local_root'] for b in branches})!=4):
        raise ValueError('Global four-branch ownership/contract interval differs')
    rows=[branch_inventory(**b,hashes=hashes,production=production,snapshot=snap) for b in branches]
    nfs={};local={};nfs_paths={};local_paths={};certs={};expected_local=[];issues=[];residues=[]
    for b,row in zip(branches,rows):
        root=Path(row['root']);loc=Path(row['local_root']);policy=b['policy']
        if policy['nfs_keep']!=3 or policy['local_keep']!=15 or policy.get('adaptive_local_pruning') is not True:
            raise ValueError('Global retention must be approved3/adaptive15')
        if set(certs)&set(row['certificates']):raise ValueError('Repeated absolute checkpoint across branches')
        certs.update(row['certificates']);nfs.update(row['nfs']);local.update(row['local'])
        nfs_paths.update({name:str(root/'training'/name) for name in row['nfs']})
        local_paths.update({name:str(loc/'checkpoints'/name) for name in row['local']})
        residues.extend(row['residues'])
        ancestor=existing_ancestor(loc)
        if ancestor.stat().st_dev==root.stat().st_dev:issues.append('NFS/local filesystem identity overlaps')
    ordered=sorted(certs);wanted_nfs=ordered[-3:];older=set(ordered[:-3])
    for row in rows:
        own_older=older & set(row['certificates']);journal=row['journal']
        if own_older!=set(journal['nfs_retired']):issues.append('Branch NFS retirement receipts differ from global removed history')
        eligible=own_older-set(journal['local_retired'])
        if journal['history_loss_epochs']:eligible &= set(journal['nfs_retired_after_last_loss'])
        if row['local_namespace_missing']:eligible=set()
        expected_local.extend(eligible)
        if set(row['local'])!=eligible:issues.append('Local history differs from its owner journal/loss epoch')
    overlap=sorted(set(nfs)&set(local));expected_local=sorted(expected_local)
    if sorted(nfs)!=wanted_nfs:issues.append('Global NFS is not latest3 certified checkpoints')
    if overlap:issues.append('Checkpoint duplicates remain across tiers')
    if len(local)>15 or len(set(nfs)|set(local))>18:issues.append('Global normal history exceeds3+15')
    if residues:issues.append('Unfinished or unverified retention residues exist')
    root=Path(new['root']);manager=snap.read(root/'global_retention_state.json')
    if snap.read(root/'retention_state.json')!=manager:issues.append('Global/primary retention state differs')
    for key,value in [('nfs_checkpoints',sorted(nfs)),('nfs_paths',nfs_paths),('uncertified_nfs',[])]:
        if manager.get(key)!=value:issues.append('Manager differs from observed '+key)
    # Whole optional local namespace loss after manager publication is allowed;
    # surviving branches and every previously recorded path must still match.
    missing={r['local_root'] for r in rows if r['local_namespace_missing']}
    recorded=manager.get('local_paths',{})
    for name,path in recorded.items():
        owners=[r for r in rows if name in r['certificates']]
        if len(owners)!=1 or path!=str(Path(owners[0]['local_root'])/'checkpoints'/name):
            issues.append('Manager local path ownership differs')
    known={n:p for n,p in recorded.items() if str(Path(p).parent.parent) not in missing}
    if known!=local_paths:issues.append('Manager surviving local paths differ')
    if not missing:
        for key,value in [('local_checkpoints',sorted(local)),('distinct_checkpoints',len(set(nfs)|set(local)))]:
            if manager.get(key)!=value:issues.append('Manager differs from observed '+key)
    snap.unchanged()
    if final and issues:raise ValueError('Final global retention audit: '+'; '.join(issues))
    return dict(status=('passed_final_budget_retention_content' if production else 'passed_fixture_budget_retention_content') if final else 'observed_budget_retention_snapshot',
        production=production,final=final,full_payload_sha_refreshed=hashes,through_step=through_step,
        nfs=nfs,local=local,nfs_paths=nfs_paths,local_paths=local_paths,
        expected_nfs=wanted_nfs,expected_local=expected_local,nfs_resume_candidates=sorted(nfs),
        distinct_checkpoints=len(set(nfs)|set(local)),issues=issues,residues=residues,overlap=overlap,
        branch_inventory=rows,certificate_count=len(certs),
        local_history_optional_loss=bool(missing) or any(r['journal']['history_loss_epochs'] for r in rows),
        protected_parent_counted_as_normal_history=False,
        resident_file_stats=dict(snap.checkpoints),
        metadata_evidence={artifact_path(Path(p)):v[1] for p,v in snap.files.items()},
        observed_directories=dict(snap.directories),
        scope='Four owners/original contract IDs, global3/adaptive15. Fresh resident hashes; deleted history cannot be rehashed. No repair/pruning.')


def audit_run(source,*,final=False,independent_runtime=None,final_storage=None):
    from scripts.supervise_budget_training import require_authorization
    from train.budget_horizon_handoff import validate_configuration,load_handoff
    from scripts.extended_horizon_storage import enforce_shared_space
    from scripts.seal_horizon_handoff import require_parent_absent,unchanged
    root=exact_path(resolve(source));snap=Snapshot()
    c=snap.read(root/'contract.json');cfg=snap.read(root/'main.json')
    budget=c
    storage=None
    if final_storage is not None:
        if not final or independent_runtime is not None:
            raise ValueError('Separate storage requires final original-source audit')
        from scripts.completed_budget_storage import bind,view as storage_view
        storage=bind(root,final_storage['control_dir'])
        if storage['reference']!=final_storage:
            raise ValueError('Final retention storage reference changed')
        budget=storage_view(root,final_storage)
        for path in storage['inputs']:snap.read(resolve(path))
    require_authorization(c);validate_configuration(cfg,c)
    if c['configurations']!={'main.json':sha256_file(root/'main.json')}:raise ValueError('Main configuration binding differs')
    unchanged({**c['code'],**c['evidence']})
    view=None
    if independent_runtime is None:
        receipt,candidate,proof=load_handoff(root,c,cfg)
    else:
        if not final:raise ValueError('Independent retention route requires complete final audit')
        from scripts.budget_independence_unavailable import source_view,admitted_candidate
        view=source_view(root,independent_runtime);candidate=admitted_candidate(root,independent_runtime)
        receipt=snap.read(root/'parent_handoff.json');proof=snap.read(root/'parent_certificate.json')
    begin=receipt['parent_metadata']['step']
    definitions=c['retention_branches'];approval_path=resolve(c['retention_authorization']);approval=snap.read(approval_path)
    if (sha256_file(approval_path)!=c['retention_authorization_sha256'] or approval.get('authorized') is not True or
            approval.get('retention_transfer_authorized') is not True or approval.get('nfs_keep')!=3 or
            approval.get('local_max_keep')!=15 or approval.get('adaptive_local_oldest_first') is not True or
            len(definitions)!=4 or [resolve(d['run']) for d in definitions]!=[resolve(p) for p in approval['ordered_runs']] or
            resolve(definitions[-1]['run'])!=root or approval['boundary_step']!=begin):
        raise ValueError('Exact four-owner retention approval differs')
    from scripts.budget_final_lineage import contract_intervals
    contract_intervals(c)
    certs=list((root/'checkpoint_certificates').glob('ckpt_*.json'))
    through=50000 if final else max([begin]+[int(p.stem[5:]) for p in certs])
    actual=snap.read(root/'training/training_contract.json') if (root/'training/training_contract.json').exists() else None
    if actual is not None and actual!=candidate:raise ValueError('Actual new worker contract differs')
    if certs and actual is None:raise ValueError('New certificates lack actual worker contract')
    previous=snap.read(root/'lineage/previous_lineage.json')
    extension=snap.read(root/'lineage/extension_metadata.json')
    historical=[previous['original_contract.json'],extension['training_contract'],receipt['parent_metadata']['training_contract']]
    branches=[];protected=[]
    for index,d in enumerate(definitions):
        branch=exact_path(resolve(d['run']))
        if index<3:
            oc=snap.read(branch/'contract.json');bcfg=snap.read(branch/'main.json');parent=snap.read(branch/'training/training_contract.json')
            if sha256_file(branch/'contract.json')!=d['contract_sha256'] or oc['tiered_retention']!=d['policy']:
                raise ValueError('Historical owner contract/policy differs')
            if oc['configurations']!={'main.json':sha256_file(branch/'main.json')}:raise ValueError('Historical config binding differs')
            require_parent_absent(d['stopped_capture'],branch)
            expected=historical[index]
            if parent!=expected:raise ValueError('Historical owner training identity differs from sealed ancestry')
            if index in (1,2):
                legacy=snap.read(branch/'parent_handoff.json');lp=snap.read(branch/'parent_certificate.json')
                lm=legacy['parent_metadata']
                if lm['training_contract']!=historical[index-1]:
                    raise ValueError('Historical protected-parent identity differs')
                protected.append((resolve(legacy['parent_checkpoint']),lp,historical[index-1],
                                  lm['config'],legacy['parent_stats']))
        else:
            bcfg=cfg;parent=candidate
            if d['policy']!=c['tiered_retention']:raise ValueError('New policy differs')
        branches.append(dict(root=branch,policy=d['policy'],parent=parent,config=bcfg,
            first_step=d['first_step'],last_step=through if index==3 else d['last_step']))
    protected.append((resolve(receipt['parent_checkpoint']),proof,receipt['parent_metadata']['training_contract'],
                      receipt['parent_metadata']['config'],receipt['parent_stats']))
    with ExitStack() as stack:
        for b in sorted(branches,key=lambda b:str(b['root'])):
            lock=stack.enter_context((b['root']/'retention.lock').open('r'));fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
        result=inventory_group(branches,through_step=through,hashes=final,final=final,snapshot=snap)
        parent_info=[];retired_parents=[]
        if view is None:
            for path,cert,contract,config,stats in protected:
                info=checkpoint_content(path,cert,contract,config,hashes=final,production=True,snapshot=snap)
                if file_stats(path)!=stats:raise ValueError('Protected parent changed')
                parent_info.append(dict(path=str(path),**info))
        else:
            from scripts.independent_parent_retention import verify_retired_parents
            retired_parents=verify_retired_parents(view,snap)
        training_audit=None
        if final:
            if view is None:
                from scripts.audit_budget_training import audit_run as audit_training
                training_audit=audit_training(root,50000)
            else:
                from scripts.budget_independence_unavailable import completed_source
                _,_,_,training_audit=completed_source(root,independent_runtime)
            report=snap.read(root/'report.json')
            if (report['certificate_receipts']!=result['branch_inventory'][3]['certificate_receipts'] or
                    result['certificate_count']!=100 or 'ckpt_0050000' not in result['nfs_resume_candidates']):
                raise ValueError('Final50000 certificate schedule/supervisor coverage differs')
        capacity=enforce_shared_space(root,budget);unchanged({**c['code'],**c['evidence']});snap.unchanged()
    result.update(utc=datetime.now(timezone.utc).isoformat(),source_run=artifact_path(root),
        protected_parents=parent_info,training_audit=training_audit,capacity=capacity,goal_complete=False,
        resident_file_stats=dict(snap.checkpoints),observed_directories=dict(snap.directories),
        metadata_evidence={artifact_path(Path(p)):v[1] for p,v in snap.files.items()},
        code={p:sha256_file(resolve(p)) for p in ('scripts/audit_budget_retention.py','scripts/audit_budget_training.py','scripts/budget_final_lineage.py')})
    if view is not None:
        from scripts.independent_parent_retention import CODE as retirement_code
        from scripts.budget_independence_unavailable import CODE as source_code
        result.update(independent_runtime=independent_runtime,retired_parents=retired_parents,
            protected_nfs_payload_count=0,total_nfs_checkpoint_count=len(result['nfs']))
        result['code'].update({p:sha256_file(resolve(p)) for p in [*retirement_code,*source_code]})
        view.unchanged()
    if storage is not None:
        result.update(final_storage=final_storage,protected_nfs_payload_count=len(parent_info),
                      total_nfs_checkpoint_count=len(result['nfs'])+len(parent_info))
        result['code'].update(storage['code'])
    return result
