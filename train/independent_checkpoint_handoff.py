"""Read-only preparation for independent own-checkpoint resume.

Rechecks the sealed lineage and actual transition report without opening parent
payloads or original local proof paths. This module does not authorize production
adoption, start/stop a worker, remove sources, or claim a successful GPU restore.
"""
import json
from pathlib import Path
from common.data_contracts import resolve, sha256_file, identity
from train.parent_checkpoint_resume import canonical
from train.extended_handoff import validate_configuration, require_waiting_controllers_absent
from train.extended_horizon_worker_contract import BRIDGE_CODE, bound_candidate, verify_bridge_files
from scripts.extended_horizon_lineage import verify_lineage
from scripts.seal_horizon_handoff import require_parent_absent
from scripts.tiered_checkpoint_retention import inventory
from train.final_resume_probe import file_stats
from scripts.extended_horizon_storage import enforce_shared_space
from scripts.audit_main_training import verify_restart_metadata


def read_revision(main, contract, path):
    path=canonical(resolve(path))
    if path.stat().st_dev!=main.stat().st_dev:
        raise ValueError('Independent resume review must remain on NFS')
    r=json.loads(path.read_text())
    if (r.get('purpose')!='independent_nfs_resume_input_review_v1' or
            r.get('source_run')!=str(main) or r.get('production_adoption_authorized') is not False or
            r.get('source_contract_sha256')!=sha256_file(main/'contract.json') or
            json.loads((main/'contract.json').read_text())!=contract):
        raise ValueError('Independent review contract/source binding differs')
    for name,digest in r['code'].items():
        if sha256_file(canonical(resolve(name)))!=digest:
            raise ValueError('Independent review implementation changed')
    name='train/independent_checkpoint_handoff.py'
    if r['code'].get(name)!=sha256_file(resolve(name)):
        raise ValueError('Independent review implementation is not bound')
    r['_revision_directory']=str(path.parent)
    r['_revision_sha256']=sha256_file(path)
    return r


def review_child(main, config, candidate, revision, parent_step):
    spec=revision['child'];step=spec['step']
    if type(step) is not int or not parent_step<step<=57000 or step%500:
        raise ValueError('Certified own checkpoint must advance the parent')
    path=main/'training'/f'ckpt_{step:07d}'
    cert=main/'checkpoint_certificates'/(path.name+'.json')
    if canonical(spec['checkpoint'])!=path or path.stat().st_dev!=main.stat().st_dev:
        raise ValueError('Own resume checkpoint must be in exact NFS training scope')
    if sha256_file(cert)!=spec['certificate_sha256']:
        raise ValueError('Own checkpoint certificate changed')
    proof=json.loads(cert.read_text())
    if (proof.get('status')!='passed_main_checkpoint_storage' or proof['step']!=step or
            proof['training_contract_id']!=candidate['id'] or canonical(resolve(proof['checkpoint']))!=path or
            sum(r['path'].endswith('.distcp') for r in proof['files'])!=8):
        raise ValueError('Own checkpoint certificate identity differs')
    before=file_stats(path)
    inventory(path,proof,hashes=True,workers=8)
    meta=json.loads((path/'meta.json').read_text())
    verify_restart_metadata(meta,config,candidate,step)
    if file_stats(path)!=before:
        raise ValueError('Own checkpoint changed during review')
    return dict(path=str(path),step=step,certificate_sha256=spec['certificate_sha256'],
                file_stats=before,full_payload_sha_refreshed=True,bytes=proof['bytes'],
                model_optimizer_rng_restore_tested=False)


def review_validation_evidence(main, evidence, revision):
    backups=revision['local_proof_backups']
    # The original local paths may be gone. Identify their allowed scope from
    # the pinned storage contract, not by accessing/stat'ing disposable files.
    contract=json.loads((main/'contract.json').read_text())
    roots=[canonical(p) for p in contract['shared_storage']['local_temp_paths']]
    expected={p:h for p,h in evidence.items() if any(canonical(resolve(p)).is_relative_to(r) for r in roots)}
    if set(backups)!=set(expected):
        raise ValueError('Exact local proof replacement coverage differs')
    for path,digest in evidence.items():
        source=canonical(resolve(path))
        if path in backups:
            entry=backups[path];source=canonical(entry['backup'])
            if (entry['sha256']!=digest or not source.is_relative_to(Path(revision['_revision_directory'])) or
                    source.stat().st_dev!=main.stat().st_dev):
                raise ValueError('Proof backup scope/digest differs')
        elif source.stat().st_dev!=main.stat().st_dev:
            raise ValueError('Unreviewed local dependency')
        if sha256_file(source)!=digest:
            raise ValueError('Underlying8GPU validation evidence changed')


def review_handoff(root, contract, config, revision_path):
    root=canonical(root);main=canonical(resolve(contract['main_run']))
    if root.parent!=canonical(resolve('runs')) or main.parent!=root.parent:
        raise ValueError('Exact independent permanent run namespaces required')
    if root.stat().st_dev!=main.stat().st_dev:
        raise ValueError('Run metadata must remain on permanent storage')
    revision=read_revision(main,contract,revision_path)
    if contract['purpose']!='main_training':
        raise ValueError('Independent review requires the exact production source')
    validate_configuration(config,contract)
    if (contract['configurations']!={'main.json':sha256_file(main/'main.json')} or
            config!=json.loads((main/'main.json').read_text())):
        raise ValueError('Independent review configuration differs')
    out=canonical(resolve(config['out_dir']))
    if root!=main or out!=main/'training':raise ValueError('Production output namespace differs')
    receipt_path=root/'parent_handoff.json'
    if contract['evidence'].get(str(receipt_path))!=sha256_file(receipt_path):
        raise ValueError('Unbound extension handoff')
    receipt=json.loads(receipt_path.read_text());binding=contract['extension_execution']
    if (receipt.get('status')!='sealed_extended_parent_handoff' or
            receipt.get('execution_binding')!=binding or
            canonical(resolve(receipt['protection_root']))!=main):
        raise ValueError('Unsealed or mismatched extension handoff')
    verify_bridge_files(binding)
    if any(contract['code'].get(n)!=binding['code'][n] for n in BRIDGE_CODE):
        raise ValueError('Outer/inner extension code binding differs')
    for path,digest in {**contract['code'],**contract['evidence']}.items():
        if sha256_file(canonical(resolve(path)))!=digest:
            raise ValueError('Bound execution evidence changed: '+path)
    meta=receipt['parent_metadata'];original=receipt['original_contract']
    candidate=bound_candidate(meta,original,binding)
    if contract['generation_steps']!=candidate['monitored_evaluation']['generation_steps']:
        raise ValueError('Outer generation schedule differs')
    parent=canonical(resolve(receipt['parent_checkpoint']))
    if parent!=main/'parent_checkpoint'/f"ckpt_{meta['step']:07d}":
        raise ValueError('Protected parent outside permanent new-run scope')
    proof_path=main/'parent_certificate.json'
    if sha256_file(proof_path)!=binding['parent_certificate_sha256']:
        raise ValueError('Protected parent certificate bytes changed')
    proof=json.loads(proof_path.read_text())
    if (identity(proof)!=binding['parent_certificate_id'] or proof.get('step')!=meta['step'] or
            proof.get('training_contract_id')!=meta['training_contract']['id']):
        raise ValueError('Protected parent certificate identity differs')
    # Parent metadata/certificate remain bound on NFS; payload is never opened.
    child=review_child(main,config,candidate,revision,meta['step'])
    source=canonical(resolve(receipt['source_run']));ancestor=canonical(resolve(receipt['original_run']))
    if len({main,source,ancestor})!=3 or source.parent!=main.parent or ancestor.parent!=main.parent:
        raise ValueError('Three distinct run namespaces required')
    require_parent_absent(receipt['source_capture'],source)
    require_parent_absent(receipt['original_capture'],ancestor)
    require_waiting_controllers_absent(receipt['stopped_waiting_controllers'])
    lineage=main/'lineage'
    if sha256_file(lineage/'manifest.json')!=binding['parent_ledger_manifest_sha256']:
        raise ValueError('Two-prefix lineage manifest changed')
    probe=json.loads((root/'probe/probe.json').read_text())
    prompts=[json.loads(s) for s in resolve(contract['prompts']).read_text().splitlines() if s.strip()]
    if (sha256_file(root/'probe/probe.json')!=candidate['monitored_evaluation']['probe_sha256'] or
            sha256_file(resolve(contract['prompts']))!=candidate['monitored_evaluation']['prompts_sha256']):
        raise ValueError('Probe/prompt identity differs')
    manifest,_=verify_lineage(lineage,meta,original,probe,prompts)
    if canonical(resolve(manifest['source_run']))!=source:
        raise ValueError('Lineage source namespace differs from stopped producer')
    if contract['purpose']=='main_training':
        validation_path=canonical(resolve(contract['extension_validation_report']))
        if contract['evidence'].get(str(validation_path))!=sha256_file(validation_path):
            raise ValueError('Actual8GPU validation report not bound')
        validation=json.loads(validation_path.read_text())
        if (validation.get('status')!='passed_actual_8rank_extension_resume' or
                validation.get('world_size')!=8 or validation.get('parent_step')!=meta['step'] or
                validation.get('parent_contract_id')!=meta['training_contract']['id'] or
                validation.get('child_contract_id')!=candidate['id'] or
                any(validation.get(k) is not True for k in
                    ('model_optimizer_rng_exact','next_update_exact','split_resume_exact')) or
                not validation.get('evidence')):
            raise ValueError('Actual8GPU extension validation incomplete')
        review_validation_evidence(main,validation['evidence'],revision)
    enforce_shared_space(main,contract)
    if (sha256_file(resolve(revision_path))!=revision['_revision_sha256'] or
            sha256_file(main/'contract.json')!=revision['source_contract_sha256'] or
            file_stats(Path(child['path']))!=child['file_stats']):
        raise ValueError('Independent review source changed before completion')
    return dict(status='prepared_nfs_only_resume_inputs_not_runtime_accepted',
        source_run=str(main), checkpoint=child, training_contract_id=candidate['id'],
        parent_payload_read=False, original_local_evidence_read=False,
        actual8gpu_independent_resume_verified=False,
        preserved_parent_metadata_id=identity(meta), preserved_parent_certificate_id=identity(proof),
        scope='Read-only input/lineage/full-file-SHA review. Existing worker and auditors are unchanged; actual8GPU independent restore and runtime integration remain required.')

