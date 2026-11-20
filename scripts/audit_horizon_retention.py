"""Read-only global3+15 audit across exact old/new horizon ownership.

No retention manager construction, directory repair, checkpoint move or deletion.
Each branch retains its own contract and journal. Protected migration evidence
is validated separately and is never counted as one of the normal3+15 copies.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime,timezone
import fcntl
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve,artifact_path,atomic_json,sha256_file
from scripts.audit_checkpoint_retention import (Snapshot,ChangedDuringAudit,checkpoint_content,
    journal_review,existing_ancestor,NAME,GIB)
from scripts.tiered_checkpoint_retention import inventory,records,size_tree,exact_path
from train.final_resume_probe import file_stats
from train.horizon_target import TARGET_STEPS,TOTAL_TOKENS,FINAL_CHECKPOINT

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
    if config['ckpt_every']!=500 or first_step%500 or last_step%500 or first_step>last_step:
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
    if len(branches)!=2 or (final and not hashes) or (production and final and through_step!=TARGET_STEPS):
        raise ValueError('Exact two-branch final full-SHA scope required')
    snap=snapshot or Snapshot()
    source,new=branches
    if (source['first_step']!=500 or new['first_step']!=source['last_step']+500 or
            new['last_step']!=through_step or source['root']==new['root'] or
            source['parent']['id']==new['parent']['id'] or
            source['policy']['local_root']==new['policy']['local_root']):
        raise ValueError('Global branch ownership/contract interval differs')
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
    return dict(status=('passed_final_horizon_retention_content' if production else 'passed_fixture_horizon_retention_content') if final else 'observed_horizon_retention_snapshot',
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
        scope='Two owners/original contract IDs, global3/adaptive15. Fresh resident hashes; deleted history cannot be rehashed. No repair/pruning.')


def audit_run(source, *, final=False):
    from scripts.supervise_horizon_training import require_authorization
    from train.monitored_horizon import validate_configuration,load_handoff
    from scripts.horizon_storage_plan import enforce_runtime_space
    from scripts.seal_horizon_handoff import require_parent_absent
    root=exact_path(resolve(source));snap=Snapshot()
    c=snap.read(root/'contract.json');cfg=snap.read(root/'main.json')
    require_authorization(c);validate_configuration(cfg,c)
    if c['configurations']!={'main.json':sha256_file(root/'main.json')}:raise ValueError('New training config binding differs')
    for path,h in {**c['code'],**c['evidence']}.items():
        if sha256_file(resolve(path))!=h:raise ValueError('Changed new training binding: '+path)
    receipt,candidate,parent_proof=load_handoff(root,c,cfg)
    child=snap.read(root/'training/training_contract.json')
    if child!=candidate:raise ValueError('Actual child training identity differs')
    legacy=c['legacy_retention'];old=exact_path(resolve(legacy['source_run']))
    approval_path=exact_path(resolve(legacy['authorization']));approval=snap.read(approval_path)
    if (sha256_file(approval_path)!=legacy['authorization_sha256'] or approval.get('authorized') is not True or
            approval.get('retention_transfer_authorized') is not True or resolve(approval['source_run'])!=old or
            resolve(approval['new_run'])!=root or approval.get('nfs_keep')!=3 or approval.get('local_max_keep')!=15 or
            approval.get('adaptive_local_oldest_first') is not True):
        raise ValueError('Exact global retention authorization missing')
    oc=snap.read(old/'contract.json');ocfg=snap.read(old/'main.json');parent=snap.read(old/'training/training_contract.json')
    if (sha256_file(old/'contract.json')!=legacy['source_contract_sha256'] or
            oc['tiered_retention']!=legacy['source_policy'] or legacy['parent_step']!=receipt['parent_metadata']['step'] or
            parent!=receipt['parent_metadata']['training_contract'] or
            oc['configurations']['main.json']!=sha256_file(old/'main.json')):
        raise ValueError('Original policy/parent/config binding differs')
    require_parent_absent(legacy['stopped_capture'],old)
    training_audit=None
    if final:
        from scripts.evaluate_final_horizon import completed_main
        _,_,_,training_audit=completed_main(root);through=TARGET_STEPS
    else:
        certs=list((root/'checkpoint_certificates').glob('ckpt_*.json'))
        if not certs:raise ValueError('No child checkpoint yet; no global audit candidate')
        through=max(int(p.stem[5:]) for p in certs)
    branches=[dict(root=old,policy=legacy['source_policy'],parent=parent,config=ocfg,first_step=500,last_step=legacy['parent_step']),
        dict(root=root,policy=c['tiered_retention'],parent=child,config=cfg,first_step=legacy['parent_step']+500,last_step=through)]
    # Read existing locks only. Never instantiate managers or create a missing
    # namespace in an audit, even when local history was lost.
    with ExitStack() as stack:
        for branch in sorted(branches,key=lambda b:str(b['root'])):
            lock=stack.enter_context((branch['root']/'retention.lock').open('r'))
            fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
        result=inventory_group(branches,through_step=through,hashes=final,final=final,snapshot=snap)
        protected=exact_path(resolve(receipt['parent_checkpoint']))
        info=checkpoint_content(protected,parent_proof,parent,ocfg,hashes=final,production=True,snapshot=snap)
        if file_stats(protected)!=receipt['parent_stats']:raise ValueError('Protected migration parent changed')
        if final:
            report=snap.read(root/'report.json')
            if report['certificate_receipts']!=result['branch_inventory'][1]['certificate_receipts']:
                raise ValueError('Final child supervisor certificate coverage differs')
            if FINAL_CHECKPOINT not in result['nfs_resume_candidates'] or result['certificate_count']!=70:
                raise ValueError('Final global35000/70 checkpoint coverage missing')
        capacity=enforce_runtime_space(root,c)
        used=sum(size_tree(Path(b['policy']['local_root'])) for b in branches)
        if used>c['tiered_retention']['local_budget_bytes']:raise ValueError('Combined local history budget exceeded')
        if size_tree(old)>oc['tiered_retention']['nfs_budget_bytes']:raise ValueError('Original evidence/residue budget exceeded')
        for path,h in {**c['code'],**c['evidence']}.items():
            if sha256_file(resolve(path))!=h:raise ChangedDuringAudit('Bound implementation/evidence changed')
        snap.unchanged()
        result.update(utc=datetime.now(timezone.utc).isoformat(),source_run=artifact_path(root),
            source_contract_sha256=sha256_file(root/'contract.json'),audit_code_sha256=sha256_file(Path(__file__)),
            training_audit=training_audit,protected_parent=dict(path=str(protected),**info),capacity=capacity,
            combined_local_used_bytes=used,goal_complete=False,
            resident_file_stats=dict(snap.checkpoints),observed_directories=dict(snap.directories),
            metadata_evidence={artifact_path(Path(p)):v[1] for p,v in snap.files.items()})
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source-run',required=True)
    p.add_argument('--output',required=True);p.add_argument('--final',action='store_true');args=p.parse_args()
    output=resolve(args.output);source=resolve(args.source_run)
    c=json.loads((source/'contract.json').read_text())
    protected=[source,resolve(c['legacy_retention']['source_run'])]
    protected.extend(Path(p[k]) for p in [c['tiered_retention'],c['legacy_retention']['source_policy']] for k in ['local_root','temp_root'])
    if output.exists() or any(output.is_relative_to(p) or p.is_relative_to(output) for p in protected):
        raise ValueError('Use a new independent output directory')
    result=audit_run(source,final=args.final);output.mkdir(parents=True,exist_ok=False)
    atomic_json(output/'report.json',result)
    if args.final:atomic_json(output/'.done',dict(report_sha256=sha256_file(output/'report.json')))
