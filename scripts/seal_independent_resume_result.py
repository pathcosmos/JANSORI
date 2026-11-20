"""Historical acceptance of a freshly verified independent8GPU diagnostic.

Never restores permissions, stops/starts training or retires checkpoints.
Replays archived rank/runtime/lineage evidence, not current tensor availability.
"""
import hashlib
import json
import os
from pathlib import Path

from common.data_contracts import resolve,artifact_path,sha256_file,identity
from scripts.tiered_checkpoint_retention import exact_path,atomic_json,fsync_dir
from scripts.audit_main_training import Snapshot
from scripts.freeze_independent_prefix import ArchivedSnapshot,MAX_TOTAL,MAX_FILE,replay

IMPLEMENTATIONS=['scripts/seal_independent_resume_result.py','scripts/freeze_independent_prefix.py',
    'scripts/run_independent_resume.py','scripts/verify_independent_resume_probe.py',
    'train/independent_resume_probe.py','scripts/independent_resume_isolation.py']
IMPLEMENTATIONS.append('train/independent_operational_admission.py')


def archived_isolation(spec,snap):
    binding=spec['plan']['dependency_isolation'];root=resolve(binding['root'])
    plan=snap.read(root/'plan.json');prepared=snap.read(root/'prepared.json');proof=snap.read(root/'isolated.json')
    if (binding['mode']!='live_inplace_dac' or plan['purpose']!='inplace_independent_resume_dac_isolation_v1' or
            plan['authorized'] is not True or plan['dependencies']!=spec['plan']['required_absent_paths'] or
            resolve(plan['diagnostic_run'])!=resolve(spec['plan']['run_dir']) or
            resolve(plan['main_run'])!=resolve(spec['plan']['main_run']) or
            resolve(plan['revision'])!=resolve(spec['plan']['revision']) or
            plan['source_capture']!=spec['plan']['source_capture'] or
            prepared!=dict(plan_sha256=hashlib.sha256(snap.raw(root/'plan.json')).hexdigest(),plan_id=identity(plan)) or
            proof['plan_id']!=identity(plan) or proof['plan_sha256']!=prepared['plan_sha256'] or
            proof['status']!='verified_original_dependencies_dac_denied' or proof['uid']!=plan['uid']):
        raise ValueError('Archived isolation source/identity differs')
    for name,h in binding['evidence'].items():
        if hashlib.sha256(snap.raw(name)).hexdigest()!=h:raise ValueError('Archived DAC evidence changed')
    if binding['plan_id']!=identity(plan):raise ValueError('Archived isolation plan binding differs')
    revision=snap.read(spec['plan']['revision'])
    proofs={str(resolve(p)) for p in revision['local_proof_backups']}
    scopes=sorted({str(Path(p).parent if p in proofs else Path(p)) for p in plan['dependencies']})
    scopes=[p for p in scopes if not any(Path(p).is_relative_to(Path(q)) for q in scopes if p!=q)]
    if [e['path'] for e in plan['entries']]!=scopes:
        raise ValueError('Archived dependency scope coverage differs')
    if any(e['slot']!=identity(e['path'])[:16] for e in plan['entries']):
        raise ValueError('Archived isolation slot differs')
    expected={e['slot']:e for e in plan['entries'] if e['payload']}
    if {r['slot'] for r in proof['entries']}!=set(expected) or len(proof['entries'])!=len(expected):
        raise ValueError('Archived isolation receipt coverage differs')
    for r in proof['entries']:
        p=root/'isolation_receipts'/(r['slot']+'.json');ack=snap.read(p);e=expected[r['slot']]
        if (hashlib.sha256(snap.raw(p)).hexdigest()!=r['receipt_sha256'] or
                ack!=dict(phase='isolated',plan_id=identity(plan),slot=r['slot'],blocked_stamp=r['blocked_stamp']) or
                r['blocked_stamp']['mode']!=0 or
                any(r['blocked_stamp'][k]!=e['payload']['root'][k] for k in ['device','inode','uid','gid'])):
            raise ValueError('Archived DAC acknowledgement differs')
    if proof['hidden_payload_bytes']!=sum(e['payload']['bytes'] for e in expected.values()):
        raise ValueError('Archived isolation byte accounting differs')
    return dict(plan_id=identity(plan),historical_denial_verified=True,current_denial_verified=False)


def verify(root,*,require_marker=True):
    from scripts.run_independent_resume import verify_runtime,require_comparison
    from scripts.verify_independent_resume_probe import ledger_review
    from train.independent_resume_probe import metadata_for,MODES
    root=exact_path(resolve(root));out=root/'accepted_evidence'
    manifest=json.loads((out/'manifest.json').read_text())
    if (manifest['purpose']!='accepted_independent_resume_evidence_v1' or resolve(manifest['run'])!=root or
            set(manifest['implementation'])!=set(IMPLEMENTATIONS) or
            sum(r['bytes'] for r in manifest['files'].values())>MAX_TOTAL):
        raise ValueError('Unreviewed historical resume evidence')
    for name,h in manifest['implementation'].items():
        if sha256_file(resolve(name))!=h:raise ValueError('Historical verifier implementation changed')
    snap=ArchivedSnapshot(out,manifest)
    # Verify every archived byte, including proof fields not reinterpreted here.
    for name in manifest['files']:snap.raw(name)
    if {p.name for p in (out/'blobs').iterdir()}!={r['blob'] for r in manifest['files'].values()}:
        raise ValueError('Unexpected historical evidence residue')
    report=snap.read(root/'report.json');spec=snap.read(root/'spec.json');plan=spec['plan']
    contract=snap.read(root/'contract.json');marker=snap.read(root/'.done')
    if (marker!=dict(report_sha256=hashlib.sha256(snap.raw(root/'report.json')).hexdigest(),
                    contract_sha256=hashlib.sha256(snap.raw(root/'contract.json')).hexdigest()) or
            report['status']!='passed_actual_independent3b_8gpu_resume' or report['target_steps']!=57000 or
            report['main_target_modified'] is not False or spec['plan_id']!=identity(plan) or
            resolve(plan['run_dir'])!=root or snap.read(root/'plan.json')!=plan or
            contract['plan_sha256']!=hashlib.sha256(snap.raw(root/'plan.json')).hexdigest() or
            plan.get('frozen_prefix_required') is not True or
            plan.get('operational_consumption_required') is not True or
            report['parent_checkpoint']!=spec['parent_checkpoint'] or report['parent_step']!=plan['parent_step']):
        raise ValueError('Historical diagnostic completion binding differs')
    for name,h in report['evidence'].items():
        if hashlib.sha256(snap.raw(name)).hexdigest()!=h:raise ValueError('Historical report evidence changed')
    config=snap.read(plan['parent_config']);parent=snap.read(plan['parent_contract'])
    if (hashlib.sha256(snap.raw(plan['parent_config'])).hexdigest()!=plan['inputs'][plan['parent_config']] or
            parent['id']!=plan['parent_training_contract_id']):
        raise ValueError('Historical diagnostic input identity differs')
    frozen=spec['frozen_prefix'];directory=resolve(frozen['directory'])
    if directory!=root/'frozen_prefix':raise ValueError('Historical prefix path differs')
    prefix=replay(directory)
    if (prefix['manifest_sha256']!=frozen['manifest_sha256'] or
            sha256_file(directory/'verified.json')!=frozen['verified_sha256'] or
            prefix['through_step']!=plan['parent_step'] or resolve(prefix['source_run'])!=resolve(plan['main_run'])):
        raise ValueError('Historical prefix differs from source')
    audit=snap.read(spec['audit'])
    if plan.get('shared_admission_required') is True:
        from train.independent_resume_probe import check_shared_binding
        check_shared_binding(spec,read_bytes=snap.raw)
    if hashlib.sha256(snap.raw(spec['audit'])).hexdigest()!=spec['audit_sha256'] or audit['counts']!=prefix['counts']:
        raise ValueError('Historical prefix audit binding differs')
    paths=snap.paths(root/'ledgers')
    if {p.name for p in paths}!={f'{m}-rank{r}.json' for m in MODES for r in range(8)}:
        raise ValueError('Historical24rank ledger coverage differs')
    counts=ledger_review([snap.read(p) for p in paths],parent_step=plan['parent_step'],world=8,
        config=config,source_order=parent['source_order'],plan_id=identity(plan))
    if counts!=report['counts']:raise ValueError('Historical diagnostic accounting differs')
    verify_runtime(root,spec,snapshot=snap)
    for name in ['roundtrip_comparison','resume_comparison']:
        comparison=snap.read(root/(name+'.json'));require_comparison(comparison)
        if comparison!=report['comparisons'][name]:raise ValueError('Historical tensor comparison differs')
    for name,offset in dict(restored_parent=0,continuous_final=2,split_first=1,resumed_final=2).items():
        cert=snap.read(root/'certificates'/(name+'.json'))
        if cert['metadata']!=metadata_for(spec,offset) or resolve(cert['path'])!=Path(plan['scratch'])/name:
            raise ValueError('Historical output checkpoint identity differs')
        files=cert['files'];names=[f['path'] for f in files]
        required={'.complete','.metadata','meta.json',*[f'rng_rank{r}.pt' for r in range(8)],
                  *[f'__{r}_0.distcp' for r in range(8)]}
        if (len(set(names))!=len(names) or cert['bytes']!=sum(f['bytes'] for f in files) or
                not required<=set(names) or
                any(type(f['bytes']) is not int or f['bytes']<0 or
                    not isinstance(f['sha256'],str) or len(f['sha256'])!=64 or
                    any(ch not in '0123456789abcdef' for ch in f['sha256']) for f in files) or
                any(Path(n).is_absolute() or '..' in Path(n).parts for n in names)):
            raise ValueError('Historical output file proof differs')
    isolation=archived_isolation(spec,snap)
    snap.unchanged()
    result=dict(status='verified_historical_independent8gpu_resume_evidence',
        manifest_sha256=sha256_file(out/'manifest.json'),source_run=plan['main_run'],
        parent_step=plan['parent_step'],parent_training_contract_id=parent['id'],target_steps=57000,
        prefix_counts=prefix['counts'],diagnostic_counts=counts,isolation=isolation,
        actual_new_gpu_execution=False,current_payloads_verified=False,production_adopted=False,
        scope='Recomputed archived committed lineage and24rank runtime/diagnostic accounting. Full-state comparisons were certified before sealing. No new tensor load or current path-denial claim.')
    if require_marker and json.loads((out/'verified.json').read_text())!=result:
        raise ValueError('Historical acceptance marker differs')
    return result


def seal(root):
    from scripts.run_independent_resume import verify_completed,owned_processes
    root=exact_path(resolve(root));out=root/'accepted_evidence'
    if out.exists():raise ValueError('Preserve existing acceptance attempt')
    if owned_processes(root):raise ValueError('Diagnostic processes still alive')
    contract=json.loads((root/'contract.json').read_text())
    report=verify_completed(root,contract)  # Live complete SHA/stat/runtime/DAC gates.
    spec=json.loads((root/'spec.json').read_text());plan=spec['plan']
    if (plan.get('frozen_prefix_required') is not True or plan.get('operational_consumption_required') is not True or
            not plan.get('dependency_isolation')):
        raise ValueError('Frozen prefix and actual DAC diagnostic required')
    sources=dict(report['evidence'])
    sources.update(plan['dependency_isolation']['evidence'])
    for p in [root/'report.json',root/'spec.json',root/'contract.json',root/'plan.json',root/'.done',
              resolve(plan['parent_config']),resolve(plan['parent_contract']),resolve(plan['revision'])]:
        sources[str(p)]=sha256_file(p)
    canonical={}
    for name,h in sources.items():
        key=str(resolve(name))
        if key in canonical and canonical[key]!=h:raise ValueError('Conflicting historical source aliases')
        canonical[key]=h
    if sum(exact_path(p).stat().st_size for p in canonical)>MAX_TOTAL:
        raise ValueError('Historical evidence exceeds192MiB budget')
    out.mkdir();(out/'blobs').mkdir();rows={};stats={}
    for name,h in canonical.items():
        p=exact_path(name);before=Snapshot.stamp(p)
        if not p.is_file() or before[2]>MAX_FILE:raise ValueError('Historical metadata exceeds file budget')
        raw=p.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=h or Snapshot.stamp(p)!=before:
            raise ValueError('Historical source changed during capture')
        blob=hashlib.sha256(name.encode()).hexdigest()
        with (out/'blobs'/blob).open('xb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        rows[name]=dict(blob=blob,bytes=len(raw),sha256=h);stats[p]=before
    fsync_dir(out/'blobs')
    manifest=dict(purpose='accepted_independent_resume_evidence_v1',run=str(root),files=rows,
        implementation={p:sha256_file(resolve(p)) for p in IMPLEMENTATIONS})
    atomic_json(out/'manifest.json',manifest)
    result=verify(root,require_marker=False)
    if any(Snapshot.stamp(p)!=s for p,s in stats.items()):raise ValueError('Historical source changed before acceptance')
    if verify_completed(root,contract)!=report:raise ValueError('Live diagnostic changed before acceptance')
    atomic_json(out/'verified.json',result)
    return result
