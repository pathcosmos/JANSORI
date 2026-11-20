"""Bind HF export to actual completed35k training and final diagnostics.

No training, deletion, GPU launch or export publication occurs in this module.
LiveSource is only an in-process reuse of fresh SHA/content proofs. Recreating
it from a persisted attestation is deliberately unsupported.
"""
from dataclasses import dataclass
import json
from pathlib import Path

from common.data_contracts import resolve, artifact_path, sha256_file, identity, atomic_json
from inference.verify_package import exact
from train.final_resume_probe import file_stats, stat_record
from train.horizon_target import TARGET_STEPS, TOTAL_TOKENS, FINAL_CHECKPOINT

GIB = 2**30


def make_contract(main_run, export_root, native_run, resume_run, retention_run):
    from scripts.supervise_horizon_training import require_authorization
    from train.monitored_horizon import validate_configuration, load_handoff
    from scripts.evaluate_final_rehearsal import check_bindings
    from scripts.run_final_horizon_resume import load_plan
    main, root, native, resume, retention = [exact(resolve(p)) for p in
        (main_run, export_root, native_run, resume_run, retention_run)]
    mc = json.loads((main/'contract.json').read_text())
    cfg = json.loads((main/'main.json').read_text())
    require_authorization(mc); validate_configuration(cfg,mc)
    for path,digest in {**mc['code'],**mc['evidence']}.items():
        if sha256_file(exact(resolve(path))) != digest:
            raise ValueError('Bound main code/evidence changed: '+path)
    if (cfg['max_steps'] != TARGET_STEPS or cfg['model'] != '3b' or
            mc['configurations'] != {'main.json':sha256_file(main/'main.json')}):
        raise ValueError('Exact reviewed35k source configuration required')
    _, parent, _ = load_handoff(main,mc,cfg)
    if json.loads((main/'training/training_contract.json').read_text()) != parent:
        raise ValueError('Actual activated child identity differs')
    if (not root.is_relative_to(resolve('artifacts')) or
            mc['space_reservations']['nfs'].get(str(root)) != 100*GIB or
            any(root.is_relative_to(p) or p.is_relative_to(root) for p in (main,native,resume,retention))):
        raise ValueError('Exact independent100GiB export reservation required')
    nc,rc,tc = [json.loads((p/'contract.json').read_text()) for p in (native,resume,retention)]
    plan = load_plan(resume)
    if (nc['purpose'] != 'final_native_main_evaluation' or nc['final_step'] != TARGET_STEPS or
            rc['purpose'] != 'final_horizon_resume_controller' or
            tc['purpose'] != 'final_horizon_retention_audit_controller' or
            any(resolve(c['main_run']) != main for c in (nc,rc,tc,plan)) or
            resolve(plan['native_run']) != native or resolve(tc['native_run']) != native or
            resolve(tc['resume_run']) != resume or plan['parent_step'] != TARGET_STEPS):
        raise ValueError('Final native/resume/retention dependency target differs')
    for path,gib in [(native,8),(resume,1),(retention,1)]:
        if mc['space_reservations']['nfs'].get(str(path)) != gib*GIB:
            raise ValueError('Dependency reservation missing')
    code = dict(mc['code']); inputs = dict(mc['evidence'])
    for c in (nc,rc,tc):
        check_bindings(c)
        for target,values in ((code,c['code']),(inputs,c['inputs'])):
            for key,digest in values.items():
                if key in target and target[key] != digest:
                    raise ValueError('Conflicting bound dependency code/input')
                target[key] = digest
    names = ['inference/final_source.py','inference/hf_export.py','inference/verify_package.py',
             'scripts/audit_checkpoint_retention.py','scripts/evaluate_final_main.py','eval/run.py',
             'scripts/run_final_horizon_retention_audit.py','scripts/run_final_horizon_resume.py']
    code.update({n:sha256_file(resolve(n)) for n in names})
    paths = [main/'contract.json', main/'main.json', main/'training/training_contract.json',
             main/'authorization.json', native/'contract.json', resume/'contract.json',
             resume/'plan.json',resume/'prepared.json',retention/'contract.json',
             resolve(mc['tokenizer']),resolve(mc['prompts'])]
    inputs.update({artifact_path(p):sha256_file(p) for p in paths})
    c = dict(version=1,purpose='final_horizon_hf_export',main_run=artifact_path(main),
             export_root=artifact_path(root),native_run=artifact_path(native),
             resume_run=artifact_path(resume),retention_run=artifact_path(retention),
             target_steps=TARGET_STEPS,total_tokens=TOTAL_TOKENS,
             parent_contract_id=parent['id'],tokenizer=mc['tokenizer'],prompts=mc['prompts'],
             framework=mc['framework'],code=code,inputs=inputs,
             durable_budget_gib=100,worker_pss_gib=128,worker_max_seconds=7200,
             cpu_affinity=list(range(32,64)),cpu_threads=16,additional_local_gib=0,
             dtype='float32',source_final_required=True,vllm_required_after_hf=True,
             scope='Final35k source/diagnostic admission and HF preparation; serving and Goal completion remain separate.')
    check_bindings(c)
    return c


@dataclass
class LiveSource:
    binding: dict
    checkpoint: Path
    checkpoint_stats: dict
    immutable_stats: dict
    guard: object

    def check(self):
        self.guard()
        exact(self.checkpoint)
        if file_stats(self.checkpoint) != self.checkpoint_stats:
            raise ValueError('Final checkpoint changed after this process verified SHA')
        for name,expected in self.immutable_stats.items():
            if stat_record(exact(resolve(name))) != expected:
                raise ValueError('Source/code/input/diagnostic evidence changed: '+name)


def require_final_identity(checkpoint, proof, meta, audit, c, parent):
    main = exact(resolve(c['main_run']))
    if (exact(checkpoint) != main/'training'/FINAL_CHECKPOINT or
            audit['source_run'] != artifact_path(main) or audit['validation_source'] or
            audit['through_step'] != TARGET_STEPS or audit['target_steps'] != TARGET_STEPS or
            audit['counts']['committed_tokens'] != TOTAL_TOKENS or
            audit['counts']['committed_step_rank_pairs'] != TARGET_STEPS*8 or
            audit['counts']['successful_target_ranks'] != list(range(8)) or
            audit['counts']['child_contract_id'] != parent['id'] or
            proof['status'] != 'passed_main_checkpoint_storage' or proof['step'] != TARGET_STEPS or
            proof['training_contract_id'] != parent['id'] or exact(resolve(proof['checkpoint'])) != checkpoint or
            meta['step'] != TARGET_STEPS or meta['world_size'] != 8 or meta['training_contract'] != parent or
            parent['id'] != c['parent_contract_id'] or meta['config']['model'] != '3b'):
        raise ValueError('Actual35k NFS source/full8rank identity differs')


def certify_source(c, evidence_dir, *, guard):
    """Fresh full-state SHA + native/resume/retention revalidation before export.

    Caller runs this inside a bounded worker, after combined resource admission.
    All reads of training/diagnostic sources are immutable; new proof files go
    only under the exact previously reserved export root/source namespace.
    """
    from scripts.evaluate_final_rehearsal import check_bindings
    from scripts.evaluate_final_horizon import completed_main
    from scripts.audit_checkpoint_retention import Snapshot,checkpoint_content
    from scripts.run_native_evaluation import certified
    from scripts.evaluate_final_main import verify_result
    from scripts.run_final_horizon_resume import load_plan,fresh_inputs,verify_completed as verify_resume
    from scripts.run_final_horizon_retention_audit import verify_completed as verify_retention
    from tokenizers import Tokenizer
    if not callable(guard):
        raise ValueError('Live combined-resource guard required')
    check_bindings(c)
    current = make_contract(c['main_run'],c['export_root'],c['native_run'],c['resume_run'],c['retention_run'])
    if current != c:
        raise ValueError('Export source contract changed')
    out = exact(resolve(evidence_dir)); root = exact(resolve(c['export_root']))
    if out != root/'source' or not root.is_dir() or out.exists():
        raise ValueError('Require a new exact export source-evidence namespace')
    guard()
    main = exact(resolve(c['main_run'])); parent = json.loads((main/'training/training_contract.json').read_text())
    cfg = json.loads((main/'main.json').read_text())
    # Record all immutable bindings before their content is consumed.
    immutable = {str(exact(resolve(p))):stat_record(exact(resolve(p))) for p in {**c['code'],**c['inputs']}}
    for branch in (main,exact(resolve(c['native_run'])),exact(resolve(c['resume_run'])),exact(resolve(c['retention_run']))):
        for path in (branch/'report.json',branch/'.done'):
            immutable[str(path)] = stat_record(path)
    cp,proof,meta,audit = completed_main(main)
    require_final_identity(cp,proof,meta,audit,c,parent)
    snap = Snapshot()
    content = checkpoint_content(cp,proof,parent,cfg,hashes=True,production=True,snapshot=snap)
    if (content['model_tensors'],content['state_tensors'],content['rng_ranks'],content['payload_files'],
            content['full_payload_sha_refreshed']) != (255,1020,8,8,True):
        raise ValueError('Complete actual3B/optimizer/8RNG fresh SHA required')
    guard()
    native,resume,retention = [exact(resolve(c[k])) for k in ('native_run','resume_run','retention_run')]
    nr,nc = certified(native,'passed_actual_final3b_native_evaluation'); check_bindings(nc)
    if nr['result'] != '3b_final.json' or sha256_file(native/nr['result']) != nr['result_sha256']:
        raise ValueError('Exact native final result changed')
    result = snap.read(native/nr['result']); reference = snap.read(resolve(nc['reference_report']))
    if (sha256_file(resolve(c['tokenizer'])) != parent['mix']['tokenizer_sha256'] or
            nc['tokenizer_sha256'] != parent['mix']['tokenizer_sha256']):
        raise ValueError('Final source/evaluation/export tokenizer identity differs')
    tok = Tokenizer.from_file(str(resolve(c['tokenizer'])))
    verify_result(result,proof,tok,nc['expected_targets'],reference,expected_step=TARGET_STEPS,expected_device='cuda')
    rc = snap.read(resume/'contract.json'); rr = verify_resume(resume,rc)
    if (rr['status'] != 'passed_actual_final3b_8gpu_resume' or rr['parent_step'] != TARGET_STEPS or
            rr['target_steps'] != TARGET_STEPS or rr['main_target_modified'] is not False or
            exact(resolve(rr['parent_checkpoint'])) != cp):
        raise ValueError('Actual final8GPU restore proof differs')
    tc = snap.read(retention/'contract.json'); tr = verify_retention(retention,tc)
    if tr['through_step'] != TARGET_STEPS or FINAL_CHECKPOINT not in tr['nfs_resume_candidates']:
        raise ValueError('Final export source is not a certified NFS resume candidate')
    guard(); out.mkdir(exist_ok=False)
    plan = load_plan(resume)
    input_stats = fresh_inputs(plan,out/'fresh_input_hash.json')
    immutable.update({str(exact(resolve(p))):s for p,s in input_stats.items()})
    expected_evidence = {}
    for mapping in (audit['evidence'],rr['evidence'],tr['metadata_evidence'],tr['training_audit']['evidence']):
        for name,digest in mapping.items():
            if name in expected_evidence and expected_evidence[name] != digest:
                raise ValueError('Conflicting final diagnostic evidence digest')
            expected_evidence[name] = digest
    extra = set(expected_evidence)
    extra.update(artifact_path(p) for branch in (main,native,resume,retention) for p in (branch/'report.json',branch/'.done'))
    extra.update([artifact_path(resolve(audit['checkpoint_certificate'])),artifact_path(native/nr['result'])])
    evidence = {}
    for name in sorted(extra):
        path = exact(resolve(name)); before = stat_record(path); evidence[name] = sha256_file(path)
        if name in expected_evidence and evidence[name] != expected_evidence[name]:
            raise ValueError('Final evidence differs from verified diagnostic digest')
        if stat_record(path) != before:
            raise ValueError('Final metadata changed during source certification')
        if str(path) in immutable and immutable[str(path)] != before:
            raise ValueError('Bound final metadata changed while checking')
        immutable[str(path)] = before
    snap.unchanged(); check_bindings(c)
    binding = dict(status='verified_actual_final35k_source',checkpoint=artifact_path(cp),step=TARGET_STEPS,
        training_contract_id=parent['id'],proof_sha256=sha256_file(resolve(audit['checkpoint_certificate'])),
        tokenizer_sha256=sha256_file(resolve(c['tokenizer'])),export_contract_id=identity(c),
        committed_tokens=TOTAL_TOKENS,content=content,final_native_verified=True,
        actual8gpu_restore_verified=True,final_retention_verified=True,evidence=evidence,
        quality_success_asserted=False,official_benchmark_measured=False,goal_complete=False)
    live = LiveSource(binding,cp,snap.checkpoints[str(cp)],immutable,guard)
    live.check()
    atomic_json(out/'final_training_audit.json',audit)
    atomic_json(out/'attestation.json',dict(binding=binding,checkpoint_stats=live.checkpoint_stats,
        immutable_stats=immutable,scope='Fresh in-process admission; persisted record alone cannot recreate LiveSource.'))
    live.check()
    return live
