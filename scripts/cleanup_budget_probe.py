"""Retire only four newly created, accepted50k diagnostic payload directories.

Training parents/checkpoints and old diagnostics are never candidates. Immutable
certificates, observations, ledgers and comparison reports remain. Partial
retirement preserves a bound journal; no recursive deletion or automatic retry.
"""
import json
from pathlib import Path

from common.data_contracts import resolve,sha256_file,identity
from scripts.tiered_checkpoint_retention import exact_path,atomic_json,fsync_dir
from scripts.seal_horizon_handoff import unchanged,write_once,json_bytes
from scripts.verify_final_resume_probe import verify_inventory
from train.final_resume_probe import file_stats,stat_record

AUTH='runs/main_training_50k_plan_v1/diagnostic_cleanup_authorization_v1.json'


def admission(main):
    from scripts.run_budget_probe import load_plan,verify_certificates,gpu_pids
    from train.budget_horizon_handoff import load_handoff
    from scripts.completed_budget_storage import active_source_processes
    main=exact_path(resolve(main));authpath=resolve(AUTH);auth=json.loads(authpath.read_text())
    if (main!=resolve('runs/3b_full_50k_v1') or auth.get('authorized') is not True
            or resolve(auth.get('main_run',''))!=main
            or auth.get('scope')!='only_four_new_successful_budget_probe_checkpoint_payloads'
            or auth.get('maximum_payload_directories')!=4):
        raise ValueError('Exact new diagnostic cleanup authorization required')
    c=json.loads((main/'contract.json').read_text());cfg=json.loads((main/'main.json').read_text())
    if c['evidence'].get(str(authpath))!=sha256_file(authpath):raise ValueError('Cleanup approval not bound to execution')
    load_handoff(main,c,cfg)
    activated=json.loads((main/'activation.json').read_text())
    if (activated.get('status')!='authorized50k_execution_materialized_not_launched'
            or activated.get('contract_sha256')!=sha256_file(main/'contract.json')
            or activated.get('actual_probe_report_sha256')!=sha256_file(main/'horizon_probe/report.json')):
        raise ValueError('Validated50k activation missing')
    if active_source_processes(main) or gpu_pids():
        raise ValueError('Cleanup requires idle GPUs and not-yet-started50k main')
    plan,_=load_plan(main/'horizon_probe/plan.json')
    report=json.loads((main/'horizon_probe/report.json').read_text());marker=json.loads((main/'horizon_probe/.done').read_text())
    if (report.get('status')!='passed_actual_8rank_budget_resume'
            or marker!=dict(plan_id=identity(plan),report_sha256=sha256_file(main/'horizon_probe/report.json'))):
        raise ValueError('Actual completed four-mode8GPU comparison required')
    unchanged(report['evidence'])
    return c,plan,report


def retire_entries(entries, output, *, guard, fault=lambda point:None):
    """Exact flat files with certified stats; caller binds operational admission.

CPU tests use private tiny files. Missing files are accepted only after a
published retirement plan and staging rename; unrelated files always reject.
"""
    output=exact_path(output);planpath=output/'plan.json'
    if len(entries)!=4 or len({row['path'] for row in entries})!=4:
        raise ValueError('Exactly four distinct new payload paths required')
    guard()
    if output.exists():
        if json.loads(planpath.read_text())!=entries:raise ValueError('Changed retirement plan')
    else:
        for row in entries:
            path=exact_path(row['path']);proof=row['proof']
            if path.with_name(path.name+'.retiring-50k').exists():raise ValueError('Unowned staging residue')
            verify_inventory(path,proof,hashes=True)
        guard();output.mkdir();write_once(planpath,json_bytes(entries))
    removed=0
    for index,row in enumerate(entries):
        guard();path=exact_path(row['path']);trash=path.with_name(path.name+'.retiring-50k')
        receipt=output/f'{index}.json';proof=row['proof'];files={r['path']:r for r in proof['files']}
        if any('/' in name or name in ('','.','..') for name in files):raise ValueError('Only certified flat payload files allowed')
        if path.exists():
            if trash.exists() or receipt.exists():raise ValueError('Ambiguous payload/staging/receipt coexistence')
            verify_inventory(path,proof)
            atomic_json(output/'state.json',dict(phase='rename_intent',path=str(path),trash=str(trash)))
            path.rename(trash);fsync_dir(path.parent);fault('renamed')
        if trash.exists():
            present=file_stats(trash)
            if set(present)-files.keys():raise ValueError('Unknown staging file; preserve')
            for name,stat in present.items():
                if stat!=proof['stats'][name] or sha256_file(trash/name)!=files[name]['sha256']:
                    raise ValueError('Staged payload changed; preserve')
            for name in sorted(present):
                guard();p=exact_path(trash/name)
                if stat_record(p)!=proof['stats'][name]:raise ValueError('Payload changed before unlink')
                p.unlink();fault('file_unlinked')
            fsync_dir(trash);trash.rmdir();fsync_dir(path.parent)
        # A crash after the last unlink/rmdir can leave no receipt. The immutable
        # intent and exact source absence record that state without broad cleanup.
        if path.exists() or trash.exists():raise ValueError('Retirement did not finish')
        atomic_json(receipt,dict(status='retired_verified_new_diagnostic_payload',path=str(path),
            bytes=proof['bytes'],certificate=row['certificate'],certificate_sha256=row['certificate_sha256']))
        removed+=proof['bytes']
    guard();result=dict(status='retired_four_verified_new_50k_probe_payloads',count=4,bytes=removed,
        comparison_and_ledger_evidence_preserved=True,existing_checkpoints_modified=False,goal_complete=False)
    atomic_json(output/'report.json',result);return result


def execute(main):
    from scripts.run_budget_probe import checkpoint_targets
    from scripts.extended_horizon_storage import ancestor
    main=exact_path(resolve(main));c,plan,report=admission(main)
    entries=[];temp=exact_path(c['tiered_retention']['temp_root'])
    for mode in ('continuous','first','resumed'):
        for path,meta,cert in checkpoint_targets(plan,mode):
            path=exact_path(path);proof=json.loads(cert.read_text())
            if (not path.is_relative_to(temp) or ancestor(path).stat().st_dev==main.stat().st_dev
                    or proof['metadata']!=meta or resolve(proof['checkpoint'])!=path
                    or report['evidence'].get(str(cert),report['evidence'].get(str(cert.relative_to(resolve('.')))))!=sha256_file(cert)):
                raise ValueError('Cleanup candidate outside accepted50k diagnostic scope')
            entries.append(dict(path=str(path),certificate=str(cert),certificate_sha256=sha256_file(cert),proof=proof))
    def guard():
        admission(main)
        unchanged({row['certificate']:row['certificate_sha256'] for row in entries})
    return retire_entries(entries,main/'horizon_probe/payload_retirement',guard=guard)
