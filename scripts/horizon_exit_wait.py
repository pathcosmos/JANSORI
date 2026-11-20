"""Recovery preparation: observe pinned exits without rereading dying cmdlines.

No CLI, producer adoption, implicit recovery, or import-time signals. A caller
must explicitly authorize/bind the exact surviving paused processes. Existing
frozen handoff implementation is left unchanged.
"""
import select
import signal
import time


def wait_exit_only(bound, names, *, timeout=30):
    names = list(names)
    if (not names or len(set(names)) != len(names) or not set(names) <= bound.fds.keys()
            or not 0 < timeout <= 60):
        raise ValueError('Invalid pinned exit wait')
    deadline = time.monotonic() + timeout
    pending = set(names)
    while pending:
        pending = {name for name in pending if not bound.exited(name)}
        if not pending:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            bound._event('pinned_exit_timeout_no_extra_signal', names=names, pending=sorted(pending))
            raise TimeoutError('Pinned process exit not observed; no automatic extra signal')
        select.select([bound.fds[name] for name in pending], [], [], remaining)
    bound._event('pinned_exit_observed', names=names)
    return {name: 'exited' for name in names}


def terminate_stopped(bound, name, *, grace_seconds=30):
    """Explicit TERM/CONT of one already verified stopped process; no fallback."""
    if not 0 < grace_seconds <= 60:
        raise ValueError('Invalid termination grace')
    if name not in bound.fds or bound.states()[name] not in ('T', 't'):
        raise ValueError('Require an exact live stopped process')
    bound.signal([name], signal.SIGTERM)
    try:
        bound.signal([name], signal.SIGCONT)
    except ProcessLookupError:
        if not bound.exited(name):
            raise
    return wait_exit_only(bound, [name], timeout=grace_seconds)
