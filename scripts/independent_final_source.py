"""Explicit own-NFS source adapter for final-stage consumers.

Requires the accepted runtime, live-consumption/exit proof and a fresh complete
57000 ledger/payload audit. Never treats a preparation receipt as production
acceptance, relabels original training IDs, or mutates model checkpoints.
"""
import fcntl
from functools import lru_cache
import json
from pathlib import Path
import re

from common.data_contracts import resolve,sha256_file,artifact_path
from scripts.tiered_checkpoint_retention import exact_path
from scripts.independent_runtime_view import RuntimeView,json_file
from scripts.observe_horizon_training import rows_from_trace,summarize
from scripts.prepare_latest_independent_revision import prepare,select_latest
from scripts.audit_independent_training import audit_run

TARGET=57000
TOKENS=119537664000
CODE=['scripts/independent_final_source.py','scripts/independent_runtime_view.py',
      'scripts/audit_independent_training.py','scripts/prepare_latest_independent_revision.py']


@lru_cache(maxsize=4)
def cached_view(control,digest):
    view=RuntimeView(control)
    if view.digest!=digest:raise ValueError('Final source runtime pin differs')
    return view


def source_view(main,reference):
    if (not isinstance(reference,dict) or set(reference)!={'control_dir','runtime_contract_sha256'} or
            not isinstance(reference['control_dir'],str) or not reference['control_dir'] or
            not isinstance(reference['runtime_contract_sha256'],str) or
            not re.fullmatch('[0-9a-f]{64}',reference['runtime_contract_sha256'])):
        raise ValueError('Explicit pinned independent runtime reference required')
    control=exact_path(resolve(reference['control_dir']))
    if sha256_file(control/'runtime_contract.json')!=reference['runtime_contract_sha256']:
        raise ValueError('Final source runtime changed')
    view=cached_view(str(control),reference['runtime_contract_sha256'])
    if view.main!=exact_path(resolve(main)):raise ValueError('Final source belongs to another main run')
    view.unchanged()
    return view


def bind_source(main,control):
    control=exact_path(resolve(control));reference=dict(control_dir=str(control),runtime_contract_sha256=sha256_file(control/'runtime_contract.json'))
    view=source_view(main,reference)
    files=[view.path,resolve(view.runtime['authorization']['path'])]
    diagnostic=resolve(view.binding['independent8gpu']['run'])
    files.extend(diagnostic/folder/name for folder in ['accepted_evidence','frozen_prefix'] for name in ['manifest.json','verified.json'])
    return dict(reference=reference,code={**view.binding['code'],**{p:sha256_file(resolve(p)) for p in CODE}},
        inputs={artifact_path(p):sha256_file(p) for p in files})


def admitted_candidate(main,reference):
    """Rebuild original numerical identity without opening parent payloads."""
    from train.extended_horizon_worker_contract import bound_candidate
    view=source_view(main,reference)
    receipt=json_file(view.main/'parent_handoff.json')
    candidate=bound_candidate(receipt['parent_metadata'],receipt['original_contract'],receipt['execution_binding'])
    if (candidate!=json_file(view.main/'training/training_contract.json') or
            candidate['id']!=view.acceptance['parent_training_contract_id']):
        raise ValueError('Independent final source numerical identity differs')
    view.unchanged()
    return candidate


def verify_audit_runtime(audit,main,reference):
    """Recheck runtime/exit and immutable audit publication; no tensor reload."""
    view=source_view(main,reference);observed=view.observe();completion=observed['completion']
    source=audit.get('independent_runtime',{})
    revision=view.control/'final_source_v1/revision.json'
    if (observed.get('phase') in ('error','failed','paused') or
            audit.get('status')!='passed_independent_runtime_target_source_audit' or
            audit.get('production_runtime_verified') is not True or audit.get('parent_payload_read') is not False or
            source.get('reference')!=reference or source.get('acceptance')!=view.acceptance or
            completion is None or source.get('completion')!=completion or
            source.get('revision')!=str(revision) or source.get('revision_sha256')!=sha256_file(revision) or
            audit.get('evidence',{}).get(artifact_path(view.path))!=view.digest):
        raise ValueError('Final audit independent runtime/exit/revision binding differs')
    directory=revision.parent;marker=json_file(directory/'prepared.json')
    if marker!=dict(revision_sha256=sha256_file(revision),review_sha256=sha256_file(directory/'review.json'),production_adoption_authorized=False):
        raise ValueError('Final audit revision publication changed')
    view.unchanged()


def observation(main,reference):
    view=source_view(main,reference);observed=view.observe()
    summary=summarize(rows_from_trace(view.main/'training/log.jsonl'),view.config);step=summary['logged_step']
    if observed['phase'] in ('error','failed','paused'):
        raise RuntimeError('Independent main requires recovery; never restart from final consumer')
    if observed['completion'] is not None:
        if step!=TARGET:raise ValueError('Final source completion and observed step differ')
        return dict(complete=True,ready=True,step=step,independent_runtime=reference,
            scope='Accepted runtime target-save/exit observation; full target ledger/SHA audit still required.')
    if observed['phase']=='await_independent_execution':
        return dict(complete=False,ready=False,step=step,phase=observed['phase'])
    processes=observed['processes']
    if processes['supervisor'] is None or processes['launcher'] is None:
        raise RuntimeError('Independent main owned handles missing')
    if observed['consumption'] is None:
        return dict(complete=False,ready=False,step=step,phase='await_all8_consumption')
    if not processes['all8_consumed_live']:raise RuntimeError('Independent main rank missing')
    return dict(complete=False,ready=step>=56500,step=step,process_identities=processes,
        scope='CPU prewarm at56500; GPU evaluation only after audited57000 and idle GPU lock.')


def final_revision(view):
    """One bounded NFS metadata revision; failed attempts are preserved."""
    current=view.observe()
    if current['completion'] is None:raise ValueError('Final source runtime completion not verified')
    selected=select_latest(view.main)
    if selected['step']!=TARGET:raise ValueError('Final57000 must be the latest certified own NFS state')
    directory=view.control/'final_source_v1';template=view.binding['revision_template']
    with (view.control/'final_source.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if not directory.exists():
            prepare(view.main,template['path'],directory,template_sha256=template['sha256'])
        marker=json_file(directory/'prepared.json');revision=directory/'revision.json'
        if (marker!=dict(revision_sha256=sha256_file(revision),review_sha256=sha256_file(directory/'review.json'),production_adoption_authorized=False)):
            raise ValueError('Final source revision preparation differs; preserve residue')
        value=json_file(revision)
        if (value['derived_from_template']!=dict(path=str(resolve(template['path'])),sha256=template['sha256']) or
                value['child']['step']!=TARGET or resolve(value['child']['checkpoint'])!=view.main/'training/ckpt_0057000' or
                value['child']['certificate_sha256']!=selected['certificate_sha256'] or
                value['source_contract_sha256']!=view.binding['main_contract_sha256']):
            raise ValueError('Final source revision differs from accepted source/template')
        return revision


def completed_source(main,reference):
    view=source_view(main,reference)
    if not observation(main,reference)['complete']:raise ValueError('Final source target not complete')
    revision=final_revision(view)
    # Fresh full-state SHA, metadata/RNG,3-segment8rank/source/trace audit.
    underlying=audit_run(view.main,TARGET,revision)
    counts=underlying['counts']
    if (underlying['validation_source'] or underlying['through_step']!=TARGET or underlying['target_steps']!=TARGET or
            underlying['parent_payload_read'] is not False or
            counts['committed_tokens']!=TOKENS or counts['committed_step_rank_pairs']!=456000 or
            counts['successful_target_ranks']!=list(range(8)) or len(counts['segments'])!=3):
        raise ValueError('Final source requires complete original3segment8rank57000 accounting')
    completion=view.observe()['completion']
    if completion is None:raise ValueError('Final source completion changed during fresh audit')
    checkpoint=view.main/'training/ckpt_0057000'
    proof=json_file(resolve(underlying['checkpoint_certificate']));meta=json_file(checkpoint/'meta.json')
    if (resolve(proof['checkpoint'])!=checkpoint or proof['step']!=TARGET or
            proof['training_contract_id']!=view.acceptance['parent_training_contract_id'] or
            meta['step']!=TARGET or meta['config']['model']!='3b' or
            sha256_file(resolve(underlying['checkpoint_certificate']))!=completion['certificate_sha256']):
        raise ValueError('Final source checkpoint/meta/runtime differs')
    result=dict(underlying)
    result.update(status='passed_independent_runtime_target_source_audit',
        underlying_audit_status=underlying['status'],production_runtime_verified=True,
        independent_runtime=dict(reference=reference,acceptance=view.acceptance,completion=completion,
            revision=str(revision),revision_sha256=sha256_file(revision)),
        actual_final_gpu_restore=False,goal_complete=False,
        limitations=['Accepted runtime plus fresh fullSHA/metadata and original3segment committed accounting verified.',
            'Actual final native evaluation, final8GPU restore, retention and HF/vLLM/Ollama remain separate.'])
    result['code']={**result['code'],**{p:sha256_file(resolve(p)) for p in CODE}}
    result['evidence']={**result['evidence'],artifact_path(view.path):view.digest}
    view.unchanged()
    return checkpoint,proof,meta,result
