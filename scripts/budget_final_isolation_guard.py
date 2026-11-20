"""Point-in-time process and file-handle guard for completed50k isolation.

No permissions, source state, or files are changed. This cannot lock future
launches: the isolation controller must hold its reviewed exclusive launch lock
across guard, access changes, GPU diagnostics and restoration.
"""
from common.data_contracts import resolve
from scripts.seal_horizon_handoff import require_parent_absent
from scripts.completed_budget_storage import active_source_processes
from scripts.budget_final_diagnostic_processes import running
from scripts.archive_retire_historical_checkpoints import no_readers


def guard(main,diagnostic,capture,directories):
    main,diagnostic=resolve(main),resolve(diagnostic)
    if main==diagnostic or main.is_relative_to(diagnostic) or diagnostic.is_relative_to(main):
        raise ValueError('Diagnostic and main namespaces must be disjoint')
    directories=[resolve(p) for p in directories]
    if (not directories or len(set(directories))!=len(directories) or
            any(a!=b and a.is_relative_to(b) for a in directories for b in directories)):
        raise ValueError('Distinct reviewed isolation directories required')
    for p in directories:
        if p==main or main.is_relative_to(p) or diagnostic==p or diagnostic.is_relative_to(p):
            raise ValueError('Isolation directory would cover main or diagnostic')
    require_parent_absent(capture,main)
    if active_source_processes(main):raise ValueError('Training source still active')
    if running(diagnostic):raise ValueError('Known diagnostic process still active')
    for p in directories:no_readers(p)
    # Repeat after scanning all file descriptors to catch intervening launches.
    require_parent_absent(capture,main)
    if active_source_processes(main) or running(diagnostic):
        raise ValueError('Source or diagnostic process appeared during guard')
    return dict(status='observed_source_diagnostic_and_readers_absent',
        main_run=str(main),diagnostic_run=str(diagnostic),directories=len(directories),
        future_launch_locked=False,access_denied_verified=False,actual8gpu_verified=False,
        retirement_authorized=False,goal_complete=False)
