"""Select latest own NFS state and prepare a new, immutable review revision.

No signals, production launch, adoption, parent reads or checkpoint removal.
Older templates may refer to already rotated children; their fixed main/code
identity and NFS proof backups remain required. Output is preparation only.
"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve,sha256_file,identity
from scripts.tiered_checkpoint_retention import exact_path,NAME,inventory,atomic_json,fsync_dir,size_tree
from scripts.audit_main_training import verify_restart_metadata,Snapshot
from train.extended_handoff import validate_configuration
from train.extended_horizon_worker_contract import bound_candidate
from train.final_resume_probe import file_stats
from train.independent_checkpoint_handoff import read_revision
from train.independent_operational_admission import PreparedOperationalAdmission

GIB=2**30
MAX_METADATA=16*2**20
OWN_CODE='scripts/prepare_latest_independent_revision.py'


def checkpoint_names(main):
    directory=exact_path(main/'training');paths=sorted(directory.glob('ckpt_*'))
    for p in paths:
        if (not NAME.fullmatch(p.name) or exact_path(p)!=p or not p.is_dir() or
                p.stat().st_dev!=main.stat().st_dev or
                not (p/'.complete').is_file() or (p/'.complete').is_symlink()):
            raise ValueError('Incomplete, aliased or unrecognized NFS checkpoint; no fallback')
    if not paths:raise ValueError('No own NFS checkpoint; parent/local fallback forbidden')
    return paths


def select_latest(main):
    """Metadata/stat validation only; full payload SHA is a later gate."""
    main=exact_path(resolve(main));contract=json.loads((main/'contract.json').read_text())
    config=json.loads((main/'main.json').read_text())
    if (main.parent!=resolve('runs') or resolve(contract['main_run'])!=main or
            resolve(config['out_dir'])!=main/'training' or contract['purpose']!='main_training'):
        raise ValueError('Exact main NFS namespace required')
    validate_configuration(config,contract)
    if contract['configurations']!={'main.json':sha256_file(main/'main.json')}:
        raise ValueError('Main configuration hash differs')
    receipt=json.loads((main/'parent_handoff.json').read_text())
    candidate=bound_candidate(receipt['parent_metadata'],receipt['original_contract'],receipt['execution_binding'])
    if candidate!=json.loads((main/'training/training_contract.json').read_text()):
        raise ValueError('Original training identity differs')
    names=checkpoint_names(main);child=names[-1];step=int(child.name[5:])
    if not receipt['parent_metadata']['step']<step<=57000 or step%500:
        raise ValueError('Own checkpoint must advance parent at a certified500-step boundary')
    cert=exact_path(main/'checkpoint_certificates'/(child.name+'.json'))
    if not cert.is_file():raise ValueError('Latest NFS checkpoint is uncertified; no older fallback')
    digest=sha256_file(cert);proof=json.loads(cert.read_text());before=file_stats(child)
    if (proof.get('status')!='passed_main_checkpoint_storage' or proof['step']!=step or
            resolve(proof['checkpoint'])!=child or proof['training_contract_id']!=candidate['id'] or
            {r['path'] for r in proof['files'] if r['path'].endswith('.distcp')}!=
            {f'__{rank}_0.distcp' for rank in range(8)}):
        raise ValueError('Latest NFS certificate identity/rank coverage differs')
    inventory(child,proof)
    verify_restart_metadata(json.loads((child/'meta.json').read_text()),config,candidate,step)
    if before!=file_stats(child) or names!=checkpoint_names(main) or sha256_file(cert)!=digest:
        raise ValueError('NFS selection changed during inspection')
    return dict(checkpoint=str(child),step=step,certificate_sha256=digest,bytes=proof['bytes'],
        file_stats=before,training_contract_id=candidate['id'],
        source_contract_sha256=sha256_file(main/'contract.json'),
        checkpoint_names=[p.name for p in names],full_payload_sha_refreshed=False)


def prepare(main,template,output,*,template_sha256):
    main,template,output=map(lambda p:exact_path(resolve(p)),(main,template,output))
    if output.exists():raise ValueError('Preserve existing revision namespace, including failed attempts')
    if sha256_file(template)!=template_sha256:raise ValueError('Template hash differs')
    contract=json.loads((main/'contract.json').read_text())
    original=read_revision(main,contract,template)
    pool=contract['shared_storage']['nfs_pools'].get('transition',{})
    scopes=[exact_path(p) for p in pool.get('paths',[])]
    if (pool.get('budget_bytes')!=GIB or len(scopes)!=1 or not scopes[0].is_dir() or
            output==scopes[0] or not output.is_relative_to(scopes[0]) or
            scopes[0].stat().st_dev!=main.stat().st_dev or output.is_relative_to(main) or
            main.is_relative_to(output)):
        raise ValueError('Revision must use exact existing transition1GiB NFS pool')
    selected=select_latest(main)
    sources={};total=0
    for key,row in original['local_proof_backups'].items():
        p=exact_path(row['backup']);before=Snapshot.stamp(p)
        if (not p.is_relative_to(template.parent) or p.stat().st_dev!=main.stat().st_dev or
                p.stat().st_nlink!=1 or before[2]>MAX_METADATA or sha256_file(p)!=row['sha256']):
            raise ValueError('Template proof backup scope/hash differs')
        sources[key]=(p,before,row['sha256']);total+=before[2]
    if total>MAX_METADATA or size_tree(scopes[0])+MAX_METADATA*3>GIB:
        raise ValueError('Revision metadata/transition peak budget unavailable')
    from scripts.extended_horizon_storage import enforce_shared_space
    enforce_shared_space(main,contract)
    revision={k:deepcopy(v) for k,v in original.items() if not k.startswith('_')}
    revision['child']={k:selected[k] for k in ('step','checkpoint','certificate_sha256')}
    revision['derived_from_template']=dict(path=str(template),sha256=template_sha256)
    revision['code'][OWN_CODE]=sha256_file(resolve(OWN_CODE))
    output.mkdir(parents=True,exist_ok=False)
    atomic_json(output/'intent.json',dict(purpose='latest_own_nfs_revision_preparation',
        main_run=str(main),template=str(template),template_sha256=template_sha256,
        selection=selected,production_adoption_authorized=False))
    (output/'proof_backups').mkdir()
    for key,(source,before,digest) in sources.items():
        target=output/'proof_backups'/(identity(key)+'.json');raw=source.read_bytes()
        if Snapshot.stamp(source)!=before:raise ValueError('Proof backup source changed')
        with target.open('xb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        if sha256_file(target)!=digest:raise ValueError('Copied proof backup SHA differs')
        revision['local_proof_backups'][key]=dict(backup=str(target),sha256=digest,bytes=len(raw))
    fsync_dir(output/'proof_backups')
    revision_path=output/'revision.json';atomic_json(revision_path,revision)
    # Only now perform fresh whole-state SHA/lineage admission. Failed output
    # remains for review; never silently republish it or mutate the template.
    admission=PreparedOperationalAdmission(main,revision_path,rank=0)
    if (selected!=select_latest(main) or admission.child_stats!=selected['file_stats'] or
            sha256_file(template)!=template_sha256):
        raise ValueError('Latest checkpoint/template changed before publication')
    for source,before,_ in sources.values():
        if Snapshot.stamp(source)!=before:raise ValueError('Proof source changed before publication')
    report=dict(status='prepared_latest_own_nfs_revision_not_adopted',selection=selected,
        review=admission.review,revision_sha256=sha256_file(revision_path),
        template_sha256=template_sha256,production_adopted=False,actual8gpu_verified=False)
    atomic_json(output/'review.json',report)
    if size_tree(output)>MAX_METADATA*3 or size_tree(scopes[0])>GIB:
        raise ValueError('Revision metadata budget exceeded; preserve failed attempt')
    atomic_json(output/'prepared.json',dict(revision_sha256=sha256_file(revision_path),
        review_sha256=sha256_file(output/'review.json'),production_adoption_authorized=False))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('main-run','template','output','template-sha256'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    prepare(args.main_run,args.template,args.output,template_sha256=args.template_sha256)
