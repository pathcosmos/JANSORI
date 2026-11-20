"""Bind exact live DAC evidence to a diagnostic; never mutate dependencies.

Historical isolation evidence is deliberately insufficient for admission.
The optional legacy absent-path mode remains fail-closed for readable paths.
"""
from contextlib import contextmanager
import json
import os

from common.data_contracts import resolve, sha256_file


IMPLEMENTATIONS = ['scripts/independent_resume_dependency_access.py',
                   'scripts/independent_resume_isolation.py']


def bind_isolation(root, main, diagnostic, revision, capture):
    from scripts.independent_resume_isolation import load_plan, verify_isolated
    root, isolation = load_plan(root)
    proof = verify_isolated(root)
    expected = dict(main_run=main, diagnostic_run=diagnostic, revision=revision,
                    source_capture_path=capture)
    if any(resolve(isolation[k]) != resolve(v) for k, v in expected.items()):
        raise ValueError('Isolation belongs to a different diagnostic/source/revision/capture')
    files = [root/'plan.json', root/'prepared.json', root/'isolated.json',
             *[root/'isolation_receipts'/(e['slot']+'.json') for e in proof['entries']]]
    return dict(mode='live_inplace_dac', root=str(root), plan_id=proof['plan_id'],
                evidence={str(p): sha256_file(p) for p in files})


def require_unavailable(plan, contract, revision):
    from train.independent_resume_probe import required_absent_paths
    from scripts.seal_horizon_handoff import require_parent_absent
    main = resolve(plan['main_run'])
    require_parent_absent(plan['source_capture'], main)
    expected = required_absent_paths(main, contract, revision)
    if plan['required_absent_paths'] != expected:
        raise ValueError('Original parent/local-proof paths must be unavailable: coverage differs')
    binding = plan.get('dependency_isolation')
    if binding is None:
        if any(os.path.lexists(p) for p in expected):
            raise ValueError('Original parent/local-proof paths must be unavailable')
        return
    if binding.get('mode') != 'live_inplace_dac':
        raise ValueError('Unsupported dependency isolation mode')
    fresh = bind_isolation(binding['root'], main, plan['run_dir'], plan['revision'],
                           plan['source_capture_path'])
    if binding != fresh:
        raise ValueError('Live isolation evidence changed')
    if plan['source_capture'] != json.loads(resolve(plan['source_capture_path']).read_text()):
        raise ValueError('Isolation process capture differs')
    for name in IMPLEMENTATIONS:
        if plan['code'].get(name) != sha256_file(resolve(name)):
            raise ValueError('Isolation implementation not pinned')
    for name, digest in binding['evidence'].items():
        if not any(resolve(p) == resolve(name) and h == digest for p,h in plan['inputs'].items()):
            raise ValueError('Isolation evidence not pinned in diagnostic inputs')


@contextmanager
def storage_view(plan):
    """Charge inaccessible existing bytes; no budget/free-space changes."""
    binding = plan.get('dependency_isolation')
    if binding is None:
        yield
        return
    from scripts.independent_resume_isolation import accounting_view
    main = resolve(plan['main_run'])
    require_unavailable(plan, json.loads((main/'contract.json').read_text()),
                        json.loads(resolve(plan['revision']).read_text()))
    with accounting_view(binding['root']):
        yield
