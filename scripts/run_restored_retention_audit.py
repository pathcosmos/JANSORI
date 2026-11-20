"""Bounded CPU-only final retention audit after actual main completion.

The approved main and both storage namespaces are read-only. A completion
marker triggers the worker; the worker must audit actual8rank57000 and freshly
hash every retained checkpoint. Interrupted evidence is preserved, never
automatically retried or repaired. This is not whole-Goal completion.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve, artifact_path, atomic_json, sha256_file
from scripts.evaluate_restored_native import main_observation
from scripts.evaluate_final_rehearsal import check_bindings
from scripts.audit_checkpoint_retention import existing_ancestor
from scripts.run_restored_final_resume import supervise_command
from scripts.tiered_checkpoint_retention import size_tree
from train.final_resume_probe import file_stats

GIB = 2**30


def make_contract(main_run, native_run, resume_run, *, runtime_control=None, storage_control=None):
    if runtime_control is not None and storage_control is not None:
        raise ValueError('Choose exactly one final source/storage route')
    main, native, resume = map(resolve, [main_run, native_run, resume_run])
    mc = json.loads((main/'contract.json').read_text())
    from scripts.supervise_extended_training import require_authorization
    require_authorization(mc)
    cfg = json.loads((main/'main.json').read_text())
    nc = json.loads((native/'contract.json').read_text())
    rc = json.loads((resume/'contract.json').read_text())
    plan = json.loads((resume/'plan.json').read_text())
    check_bindings(nc); check_bindings(rc)
    independent=None
    if runtime_control is not None:
        from scripts.independent_final_source import bind_source,source_view
        from scripts.independent_parent_retention import reviewed_plan
        from scripts.audit_checkpoint_retention import Snapshot
        independent=bind_source(main,runtime_control)
        reviewed_plan(source_view(main,independent['reference']),Snapshot())
    reference=independent['reference'] if independent else None
    if any(v.get('independent_runtime')!=reference for v in [nc,rc,plan]):
        raise ValueError('Final native/resume/retention runtime bindings differ')
    storage=None
    if storage_control is not None:
        from scripts.completed_training_storage import bind
        storage=bind(main,storage_control)
    if any(v.get('final_storage')!=(storage['reference'] if storage else None) for v in [nc,rc,plan]):
        raise ValueError('Final native/resume/retention separate storage bindings differ')
    if (cfg['model'] != '3b' or cfg['max_steps'] != 57000 or cfg['global_tokens'] != 2097152 or
            mc['configurations']['main.json'] != sha256_file(main/'main.json') or
            nc['purpose'] != 'final_native_main_evaluation' or nc.get('final_step') != 57000 or plan.get('parent_step') != 57000 or rc['purpose'] != 'restored_final_resume_controller' or
            resolve(nc['main_run']) != main or resolve(plan['main_run']) != main or
            resolve(plan['native_run']) != native or resolve(rc['main_run']) != main):
        raise ValueError('Final retention dependencies/target differ')
    names = ['scripts/run_restored_retention_audit.py', 'scripts/audit_restored_retention.py',
             'scripts/audit_restored_training.py', 'scripts/evaluate_restored_native.py',
             'scripts/run_restored_final_resume.py', 'scripts/verify_final_resume_probe.py',
             'train/restored_final_resume_probe.py', 'scripts/validate_tiered_retention_copy.py',
             'scripts/audit_checkpoint_retention.py', 'scripts/extended_horizon_storage.py',
             'scripts/horizon_probe_process.py']
    inputs = [main/'contract.json', main/'main.json', resolve(mc['retention_authorization']),
              resolve(mc['retention_revision']['authorization']),
              main/'training/training_contract.json', native/'contract.json',
              resume/'contract.json', resume/'plan.json', resume/'prepared.json']
    expected_segments=[]
    if len(mc['retention_branches'])!=3:raise ValueError('Three original owner contracts required')
    for branch,(start,end) in zip(mc['retention_branches'],[(0,8000),(8000,10000),(10000,57000)]):
        path=resolve(branch['run'])/'training/training_contract.json'
        owner=json.loads(path.read_text());inputs.append(path)
        expected_segments.append(dict(root=artifact_path(resolve(branch['run'])),contract_id=owner['id'],
                                      start_step_exclusive=start,through_step=end))
    if len({r['contract_id'] for r in expected_segments})!=3:
        raise ValueError('Original segment identities must remain distinct')
    result=dict(version=1, purpose='restored_final_retention_audit_controller', main_run=artifact_path(main),
                native_run=artifact_path(native), resume_run=artifact_path(resume),
                code={**mc['code'], **{p:sha256_file(resolve(p)) for p in names}},
                inputs={**mc['evidence'], **{artifact_path(p):sha256_file(p) for p in inputs}},
                poll_seconds=30, wait_max_seconds=22*86400, worker_max_seconds=1200,
                max_pss_gib=16, durable_budget_gib=1, local_additional_budget_gib=0,
                cpu_affinity=list(range(32)), hash_workers=8,
                env=dict(CUDA_VISIBLE_DEVICES='', OMP_NUM_THREADS='2', NUMEXPR_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2', RAYON_NUM_THREADS='1'),
                source_contract_sha256=sha256_file(main/'contract.json'),
                expected_segments=expected_segments,expected_source_order=owner['source_order'],
                retention_nfs_keep=mc['tiered_retention']['nfs_keep'],
                adaptive_local_pruning=mc['tiered_retention'].get('adaptive_local_pruning',False),
                scope='Fresh full SHA of actual policy-bounded NFS and local history; NFS-only resume and optional/adaptive local history. No main mutation, repair, pruning, GPU, upload or Goal completion.')
    if independent is not None:
        from scripts.independent_parent_retention import CODE
        result['independent_runtime']=reference
        result['code'].update(independent['code']);result['code'].update({p:sha256_file(resolve(p)) for p in CODE})
        result['inputs'].update(independent['inputs'])
        view=source_view(main,reference);path=resolve(view.binding['parent_retirement']['path'])
        result['inputs'][artifact_path(path)]=sha256_file(path)
        # Runtime acceptance imports torch/lineage evidence; do not inherit
        # the legacy waiter's512MiB cap for this distinct consumer.
        result['wait_rss_gib']=2
    if storage is not None:
        result['final_storage']=storage['reference']
        result['code'].update(storage['code']);result['inputs'].update(storage['inputs'])
    return result


def budget_contract(contract):
    main=resolve(contract['main_run'])
    if 'final_storage' in contract:
        if 'independent_runtime' in contract:
            raise ValueError('Mixed final source/storage routes')
        from scripts.completed_training_storage import view
        return view(main,contract['final_storage'])
    return json.loads((main/'contract.json').read_text())


def validate_output(root, contract):
    root = resolve(root)
    if root == resolve('runs') or not root.is_relative_to(resolve('runs')):
        raise ValueError('Use an independent run under runs')
    mc = budget_contract(contract)
    protected = [resolve(contract[k]) for k in ['main_run','native_run','resume_run']]
    protected += [Path(mc['tiered_retention'][k]) for k in ['local_root','temp_root']]
    protected += [resolve(d['run']) for d in mc['retention_branches']]
    protected += [Path(d['policy'][k]) for d in mc['retention_branches'] for k in ['local_root','temp_root']]
    if mc['shared_storage']['nfs_pools'].get('final_retention') != dict(paths=[str(root)],budget_bytes=GIB):
        raise ValueError('Exact combined final retention reservation missing')
    if any(root.is_relative_to(p) or p.is_relative_to(root) for p in protected):
        raise ValueError('Audit output overlaps a protected source')
    if any(p.is_symlink() for p in [root,*root.parents]):
        raise ValueError('Audit output symlink rejected')


def admission(root, contract):
    from scripts.resource_admission import inspect
    from scripts.extended_horizon_storage import enforce_shared_space
    hw=inspect();main=resolve(contract['main_run']);mc=budget_contract(contract)
    nc=json.loads((resolve(contract['native_run'])/'contract.json').read_text())
    rc=json.loads((resolve(contract['resume_run'])/'contract.json').read_text())
    if any(d.get('final_storage')!=contract.get('final_storage') for d in [nc,rc]):
        raise ValueError('Final dependency separate storage binding differs')
    validate_output(root,contract)
    if (hw['gpus']!=mc['gpu_identity'] or nc['durable_budget_gib']!=8 or rc['durable_budget_gib']!=1 or
            nc['host_rss_gib']!=256 or rc['worker_pss_gib']!=256 or contract['max_pss_gib']!=16):
        raise RuntimeError('Reviewed final dependency/resource identity differs')
    combined=mc['max_pss_gib']+256+256+16
    if hw['memory']['cache_aware_admission']['headroom_gib']<combined:
        raise RuntimeError('Combined final audit memory admission unavailable')
    for key,path,gib in [('final_native',resolve(contract['native_run']),8),('final_resume',resolve(contract['resume_run']),1)]:
        if mc['shared_storage']['nfs_pools'].get(key)!=dict(paths=[str(path)],budget_bytes=gib*GIB):
            raise ValueError('Exact final dependency reservation missing')
    capacity=enforce_shared_space(main,mc)
    if size_tree(root)>GIB:raise RuntimeError('Final audit metadata exceeds reserved1GiB')
    return hw,dict(**capacity,combined_memory_gib=combined,audit_additional_local_bytes=0)


def owned_workers(root):
    found = []
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            args = (proc/'cmdline').read_bytes().decode(errors='replace').split('\0')
            if ((proc/'stat').read_text().split(') ',1)[1].split()[0] != 'Z' and
                    any(a.endswith('run_restored_retention_audit.py') for a in args) and '--worker' in args and
                    '--run-dir' in args and resolve(args[args.index('--run-dir')+1]) == resolve(root)):
                found.append(int(proc.name))
        except (OSError, ValueError, IndexError):
            pass
    return sorted(found)


def reject_previous(root):
    live = owned_workers(root)
    if live:
        raise RuntimeError('Actual orphan audit handles exist: '+str(live))
    if any((root/p).exists() for p in ['execution_started.json','supervision','worker_handle.json','worker_result.json','worker_console.log']):
        raise RuntimeError('Preserve prior audit attempt and review before a new namespace')


def require_result(result, contract):
    wanted=[f'ckpt_{s:07d}' for s in [56000,56500,57000]]
    independent=contract.get('independent_runtime')
    if result.get('final_storage')!=contract.get('final_storage'):
        raise ValueError('Final retention separate storage evidence differs')
    if 'final_storage' in contract:
        budget_contract(contract)
        if result.get('protected_nfs_payload_count')!=2 or result.get('total_nfs_checkpoint_count')!=5:
            raise ValueError('Protected-parent exception must remain explicit: NFS3 plus2')
    if (result['status']!='passed_final_restored_retention_content' or result['production'] is not True or
            result['final'] is not True or result['full_payload_sha_refreshed'] is not True or
            result['source_run']!=contract['main_run'] or result['through_step']!=57000 or
            result['metadata_evidence'].get(artifact_path(resolve(contract['main_run'])/'contract.json'))!=contract['source_contract_sha256'] or
            result['code'].get('scripts/audit_restored_retention.py')!=contract['code']['scripts/audit_restored_retention.py'] or
            result['issues'] or result['residues'] or result['overlap']):
        raise ValueError('Actual final horizon retention verdict differs')
    if (sorted(result['nfs'])!=wanted or result['nfs_resume_candidates']!=wanted or result['expected_nfs']!=wanted or
            len(result['local'])>15 or result['distinct_checkpoints']!=len(result['nfs'])+len(result['local']) or
            result['certificate_count']!=114 or len(result['branch_inventory'])!=3 or
            len(result['protected_parents'])!=(0 if independent is not None else 2) or
            result['protected_parent_counted_as_normal_history'] is not False):
        raise ValueError('Actual global3+15/114 receipt coverage differs')
    all_names=set()
    for branch in result['branch_inventory']:
        names=set(branch['certificates'])
        if all_names&names:raise ValueError('Duplicate certificate across contracts')
        all_names.update(names)
        if branch['local_namespace_missing'] and branch['local']:raise ValueError('Lost local namespace counted as retained')
    combined_nfs={n:v for b in result['branch_inventory'] for n,v in b['nfs'].items()}
    combined_local={n:v for b in result['branch_inventory'] for n,v in b['local'].items()}
    if (result['nfs']!=combined_nfs or result['local']!=combined_local or
            set(combined_nfs)&set(combined_local) or sorted(combined_local)!=result['expected_local']):
        raise ValueError('Global/branch resident inventory differs')
    if all_names!={f'ckpt_{s:07d}' for s in range(500,57001,500)}:
        raise ValueError('Absolute final certificate schedule differs')
    for row,(start,end) in zip(result['branch_inventory'],[(500,8000),(8500,10000),(10500,57000)]):
        if set(row['certificates'])!={f'ckpt_{s:07d}' for s in range(start,end+1,500)}:
            raise ValueError('Original certificate owner interval differs')
    extra=[]
    if independent is not None:
        from scripts.independent_parent_retention import CODE as parent_code
        from scripts.independent_final_source import CODE as source_code
        retired=result.get('retired_parents',[])
        if (result.get('independent_runtime')!=independent or len(retired)!=2 or
                [r['step'] for r in retired]!=[8000,10000] or
                result.get('protected_nfs_payload_count')!=0 or result.get('total_nfs_checkpoint_count')!=3):
            raise ValueError('Independent final retention requires exactly3 NFS states and two proven retirements')
        if len({r['local_namespace_missing'] for r in retired})!=1:
            raise ValueError('Partially lost protected-parent archive is unverified')
        for row in retired:
            if (row['status']!='verified_retired_parent_optional_local' or row['nfs_resume_candidate'] is not False or
                    row['full_payload_sha_refreshed']!=(not row['local_namespace_missing'])):
                raise ValueError('Retired parent archive scope differs')
            if row['local_namespace_missing']:
                if row['local_content'] is not None:raise ValueError('Lost archive cannot be counted as verified content')
            else:extra.append(row['local_content'])
        for name in {*parent_code,*source_code}:
            if result['code'].get(name)!=contract['code'].get(name) or contract['code'].get(name)!=sha256_file(resolve(name)):
                raise ValueError('Independent final retention implementation differs')
    for cp in [*result['nfs'].values(),*result['local'].values(),*result['protected_parents'],*extra]:
        if (cp['model_tensors']!=255 or cp['state_tensors']!=1020 or cp['rng_ranks']!=8 or
                cp['payload_files']!=8 or cp['full_payload_sha_refreshed'] is not True):
            raise ValueError('Full3B model/optimizer/RNG fresh content coverage missing')
    audit=result['training_audit']
    expected_audit='passed_independent_runtime_target_source_audit' if independent is not None else 'passed_restored_committed_prefix_audit'
    if (audit.get('status')!=expected_audit or
            audit.get('source_run')!=contract['main_run'] or audit.get('target_steps')!=57000 or
            audit['validation_source'] or audit['through_step']!=57000 or
            audit['counts'].get('status')!='passed_ledger_accounting_only' or
            audit['counts'].get('through_step')!=57000 or audit['counts'].get('target_steps')!=57000 or
            audit['counts']['committed_step_rank_pairs']!=456000 or
            [s.get('contract_id') for s in audit['counts'].get('segments',[])]!=[b['training_contract_id'] for b in result['branch_inventory']] or
            audit['counts']['committed_tokens']!=119537664000 or
            audit['counts']['successful_target_ranks']!=list(range(8))):
        raise ValueError('Actual57k target audit missing')
    counts=audit['counts'];segments=counts['segments'];expected=contract['expected_segments']
    if (len(expected)!=3 or len({s['contract_id'] for s in expected})!=3 or
            len(segments)!=3 or [s['root'] for s in expected]!=[b['root'] for b in result['branch_inventory']]):
        raise ValueError('Bound original owner identities differ')
    source_order=contract['expected_source_order']
    if not source_order or len(source_order)!=len(set(source_order)):
        raise ValueError('Bound original source order differs')
    totals={source:0 for source in source_order}
    persisted=replayed=0
    for segment,bound,(start,end) in zip(segments,expected,[(0,8000),(8000,10000),(10000,57000)]):
        if (bound['start_step_exclusive']!=start or bound['through_step']!=end or
                segment.get('start_step_exclusive')!=start or segment.get('through_step')!=end or
                segment.get('contract_id')!=bound['contract_id'] or
                segment['committed_step_rank_pairs']!=(end-start)*8 or
                segment['committed_tokens']!=(end-start)*2097152):
            raise ValueError('Original step interval/identity accounting differs')
        tokens=segment['source_tokens']
        if (set(tokens)!=set(totals) or any(type(n) is not int or n<0 for n in tokens.values()) or
                sum(tokens.values())!=segment['committed_tokens'] or
                type(segment['replayed_tokens_through_prefix']) is not int or segment['replayed_tokens_through_prefix']<0 or
                segment['persisted_attempt_tokens_through_prefix']!=segment['committed_tokens']+segment['replayed_tokens_through_prefix']):
            raise ValueError('Original source/replay token accounting differs')
        for source in totals:totals[source]+=tokens[source]
        persisted+=segment['persisted_attempt_tokens_through_prefix'];replayed+=segment['replayed_tokens_through_prefix']
    if (counts['source_tokens']!=totals or counts['persisted_attempt_tokens_through_prefix']!=persisted or
            counts['replayed_tokens_through_prefix']!=replayed or persisted!=119537664000+replayed):
        raise ValueError('Global source/replay token accounting differs')


def verify_current(result, contract):
    check_bindings(contract);require_result(result,contract)
    main=resolve(contract['main_run']);mc=json.loads((main/'contract.json').read_text())
    branches=result['branch_inventory'];definitions=mc['retention_branches']
    if [resolve(b['root']) for b in branches]!=[resolve(d['run']) for d in definitions]:raise ValueError('Audited owner roots differ')
    policies=[d['policy'] for d in definitions]
    paths=set()
    for row,policy in zip(branches,policies):
        if row['local_root']!=policy['local_root']:raise ValueError('Audited local owner differs')
        paths.update(str(resolve(row['root'])/'training'/n) for n in row['nfs'])
        paths.update(str(Path(row['local_root'])/'checkpoints'/n) for n in row['local'])
    reference=contract.get('independent_runtime')
    if reference is None:
        protected=[]
        for branch in [resolve(definitions[1]['run']),main]:
            receipt=json.loads((branch/'parent_handoff.json').read_text())
            protected.append(str(resolve(receipt['parent_checkpoint'])))
        if [p['path'] for p in result['protected_parents']]!=protected:raise ValueError('Protected parent scope differs')
        paths.update(protected)
    else:
        from scripts.independent_final_source import source_view,verify_audit_runtime
        from scripts.independent_parent_retention import reviewed_plan,retirement_runtime_ack
        from scripts.audit_checkpoint_retention import Snapshot
        import hashlib
        view=source_view(main,reference);snap=Snapshot();_,local,details=reviewed_plan(view,snap)
        verify_audit_runtime(result['training_audit'],main,reference)
        for row,(entry,cert,_) in zip(result['retired_parents'],details):
            source=Path(entry['source']);dest=Path(entry['destination'])
            if (row['source']!=str(source) or row['destination']!=str(dest) or row['bytes']!=entry['bytes'] or
                    source.exists() or source.with_name('.archive-retiring-'+entry['slot']).exists() or
                    row['local_namespace_missing']!= (not local.exists())):
                raise ValueError('Retired parent source/archive scope changed')
            state=view.control/'parent_retirement'/entry['slot'];retirement_runtime_ack(view,state,snap)
            if (row['copy_ack_sha256']!=sha256_file(state/'copy_ack.json') or
                    row['retired_receipt_sha256']!=sha256_file(state/'retired.json')):
                raise ValueError('Reported parent retirement receipt digest differs')
            required=[state/name for name in ['entry.json','copy_ack.json','verified_files.json','retirement_intent.json','retired.json']]
            required += [state/'source_metadata'/(hashlib.sha256(r['path'].encode()).hexdigest()+'.bin') for r in cert['files'] if not r['path'].endswith('.distcp')]
            if not {artifact_path(p) for p in required} <= set(result['metadata_evidence']):
                raise ValueError('Retired parent content/receipt proof coverage missing')
            if not row['local_namespace_missing']:paths.add(str(dest))
        if not {artifact_path(Path(p)) for p in snap.files} <= set(result['metadata_evidence']):
            raise ValueError('Retirement runtime proof coverage missing')
        snap.unchanged();view.unchanged()
    if set(result['resident_file_stats'])!=paths:raise ValueError('Resident full-state proof coverage differs')
    for name,stats in result['resident_file_stats'].items():
        p=Path(name)
        if any(x.is_symlink() for x in [p,*p.parents]) or file_stats(p)!=stats:
            raise ValueError('Resident checkpoint changed after fresh SHA')
    bindings=dict(result['metadata_evidence'])
    for name,digest in result['training_audit']['evidence'].items():
        if name in bindings and bindings[name]!=digest:raise ValueError('Conflicting content proof')
        bindings[name]=digest
    for name,digest in bindings.items():
        if sha256_file(resolve(name))!=digest:raise ValueError('Audited metadata/ledger changed')
    for name,expected in result['observed_directories'].items():
        p=Path(name)
        if any(x.is_symlink() for x in [p,*p.parents]):raise ValueError('Audited namespace became symlink')
        observed=[[x.name,x.is_dir(),x.is_symlink()] for x in sorted(p.iterdir())] if p.exists() else None
        normalized=[list(r) for r in expected] if expected is not None else None
        if observed!=normalized:raise ValueError('Audited namespace changed after worker')


def worker(root):
    contract = json.loads((root/'contract.json').read_text()); check_bindings(contract)
    if 'final_storage' in contract:
        from scripts.completed_training_storage import admit_reference
        admit_reference(contract['main_run'],contract['final_storage'])
    validate_output(root,contract)
    if not (root/'execution_started.json').exists() or os.environ.get('CUDA_VISIBLE_DEVICES') != '':
        raise ValueError('Supervised CPU-only audit receipt/environment missing')
    from scripts.horizon_probe_process import OWNER_ENV
    owner=os.environ.get(OWNER_ENV);handle_path=root/'supervision/handle.json';deadline=time.monotonic()+30
    if not owner:raise ValueError('Owned audit environment missing')
    while not handle_path.exists():
        if time.monotonic()>=deadline:raise RuntimeError('Owned audit handle publication timeout')
        time.sleep(.05)
    handle=json.loads(handle_path.read_text())
    ticks=int(Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')',1)[1].split()[19])
    if handle['pid']!=os.getpid() or handle['owner']!=owner or handle['process']['start_ticks']!=ticks:
        raise ValueError('Owned audit process identity differs')
    from scripts.audit_restored_retention import audit_run
    result = audit_run(contract['main_run'],final=True,independent_runtime=contract.get('independent_runtime'),
                       **({'final_storage':contract['final_storage']} if 'final_storage' in contract else {}))
    verify_current(result,contract)
    atomic_json(root/'worker_result.json',result)


def verify_completed(root, contract):
    marker = json.loads((root/'.done').read_text())
    if marker != dict(report_sha256=sha256_file(root/'report.json'),contract_sha256=sha256_file(root/'contract.json')):
        raise ValueError('Final audit completion binding differs')
    report = json.loads((root/'report.json').read_text()); verify_current(report,contract)
    return report


def controller(root, contract):
    if 'final_storage' in contract:
        from scripts.completed_training_storage import admit_reference
        admit_reference(contract['main_run'],contract['final_storage'])
    if (root/'.done').exists():
        return verify_completed(root,contract)
    reject_previous(root); check_bindings(contract)
    (root/'pid').write_text(str(os.getpid())+'\n'); started = time.monotonic()
    try:
        while True:
            observation = main_observation(contract['main_run'],contract.get('independent_runtime'))
            if observation['complete']:
                break
            atomic_json(root/'state.json',dict(phase='running',case='await_actual_main_final',main_step=observation['step']))
            if time.monotonic()-started > contract['wait_max_seconds']:
                raise RuntimeError('Final retention wait time budget')
            rss = int(next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('VmRSS:')))*1024
            if size_tree(root)>GIB or rss>contract.get('wait_rss_gib',.5)*GIB:
                raise RuntimeError('Final retention waiting metadata/memory budget')
            time.sleep(contract['poll_seconds'])
        hw, capacity = admission(root,contract)
        atomic_json(root/'execution_hardware.json',hw); atomic_json(root/'execution_capacity.json',capacity)
        cpus = sorted(set(contract['cpu_affinity']) & set(hw['cpu']['affinity']))
        if not cpus:
            raise RuntimeError('Final retention CPU affinity unavailable')
        atomic_json(root/'execution_started.json',dict(controller_pid=os.getpid(),unix=time.time()))
        env = dict(os.environ,**contract['env'],PYTHONUNBUFFERED='1')
        command = ['taskset','-c',','.join(map(str,cpus)),sys.executable,'-u',str(Path(__file__).resolve()),'--run-dir',str(root),'--worker']
        began = time.monotonic(); measured = dict(peak_pss_bytes=0,peak_durable_bytes=0)
        policy = json.loads((resolve(contract['main_run'])/'contract.json').read_text())['tiered_retention']
        def guard(pid, elapsed, peak):
            from scripts.process_accounting import proportional_tree
            measured['peak_pss_bytes'] = max(measured['peak_pss_bytes'],proportional_tree(os.getpid())['totals_bytes']['Pss'],peak)
            measured['peak_durable_bytes'] = max(measured['peak_durable_bytes'],size_tree(root))
            if measured['peak_pss_bytes']>contract['max_pss_gib']*GIB or time.monotonic()-began>contract['worker_max_seconds']:
                raise RuntimeError('Final retention CPU audit memory/time budget')
            if (measured['peak_durable_bytes']>GIB or shutil.disk_usage(root).free<200*GIB or
                    shutil.disk_usage(existing_ancestor(policy['local_root'])).free<200*GIB):
                raise RuntimeError('Final retention proof storage/reserve budget')
            if elapsed-guard.last_admission>=30:
                _, current = admission(root,contract); atomic_json(root/'current_capacity.json',current)
                guard.last_admission = elapsed
            atomic_json(root/'state.json',dict(phase='running',case='fresh_full_retention_audit',worker_pid=pid,elapsed_seconds=time.monotonic()-began,**measured))
        guard.last_admission = -30
        metrics = supervise_command(command,root=root,env=env,guard=guard)
        if owned_workers(root):
            raise RuntimeError('Audit worker remains alive after supervision')
        result = json.loads((root/'worker_result.json').read_text()); verify_current(result,contract)
        guard(metrics['launcher_pid'],time.monotonic()-began,metrics['peak_pss_bytes'])
        result['supervision'] = dict(**metrics,controller_and_worker_peak=measured,wall_seconds=time.monotonic()-began)
        result['goal_complete'] = False
        atomic_json(root/'report.json',result)
        atomic_json(root/'.done',dict(report_sha256=sha256_file(root/'report.json'),contract_sha256=sha256_file(root/'contract.json')))
        atomic_json(root/'state.json',dict(phase='complete',case='actual_final_retention_verified',goal_complete=False,**measured))
    except BaseException as exc:
        atomic_json(root/'state.json',dict(phase='error',reason=type(exc).__name__+': '+str(exc),goal_complete=False))
        raise


def main(args):
    root = resolve(args.run_dir)
    if args.worker:
        return worker(root)
    if args.prepare:
        if root.exists():
            raise ValueError('Prepare a new independent audit namespace')
        c = make_contract(args.main_run,args.native_run,args.resume_run,runtime_control=getattr(args,'runtime_control',None),
                          storage_control=getattr(args,'storage_control',None))
        if 'final_storage' in c:
            from scripts.completed_training_storage import admit_reference
            admit_reference(c['main_run'],c['final_storage'])
        validate_output(root,c)
        hw, capacity = admission(root,c)
        root.mkdir(parents=True)
        atomic_json(root/'contract.json',c)
        atomic_json(root/'preparation_hardware.json',hw); atomic_json(root/'preparation_capacity.json',capacity)
        atomic_json(root/'prepared.json',dict(status='prepared_not_executed',contract_sha256=sha256_file(root/'contract.json'),actual_final_retention_verified=False))
        return
    c = json.loads((root/'contract.json').read_text()); validate_output(root,c)
    prepared = json.loads((root/'prepared.json').read_text())
    if prepared['contract_sha256'] != sha256_file(root/'contract.json'):
        raise ValueError('Prepared audit contract changed')
    with (root/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        def interrupted(_signum,_frame):
            raise InterruptedError('Audit controller interrupted; preserve outputs')
        previous = signal.signal(signal.SIGTERM,interrupted)
        try:
            return controller(root,c)
        finally:
            signal.signal(signal.SIGTERM,previous)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',required=True)
    p.add_argument('--main-run',default='runs/3b_full_57k_v2')
    p.add_argument('--native-run',default='runs/3b_57k_final_native_v2')
    p.add_argument('--resume-run',default='runs/3b_57k_final_resume_v2')
    p.add_argument('--runtime-control')
    p.add_argument('--storage-control')
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare',action='store_true');mode.add_argument('--run',action='store_true');mode.add_argument('--worker',action='store_true')
    main(p.parse_args())
