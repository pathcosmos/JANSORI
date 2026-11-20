"""Fail closed: old three-generation independent-runtime proofs cannot admit50k.

A future reviewed four-generation parent-retirement implementation must supply
its own actual8GPU NFS-only proof before this route is enabled. Current50k
consumers use the explicit separate storage route and retain protected parents.
"""
CODE=['scripts/budget_independence_unavailable.py']


def unavailable(*args,**kwargs):
    raise ValueError('50k independent-parent retirement is not implemented/validated; keep protected parents')

observation=completed_source=bind_source=admitted_candidate=source_view=verify_audit_runtime=unavailable
