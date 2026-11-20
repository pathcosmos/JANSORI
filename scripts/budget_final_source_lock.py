"""Use the production supervisor's existing lock during final isolation.

This locks only the cooperative source supervisor. It neither locks the final
diagnostic controller nor blocks unreviewed workers bypassing this lock; fresh
process/reader checks and a separate diagnostic execution lock remain required.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat

from common.data_contracts import resolve
from scripts.budget_final_isolation_guard import guard


@contextmanager
def completed_source_lock(main,diagnostic,capture,directories):
    main=resolve(main);path=main/'controller.lock'
    fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('Existing source supervisor lock must be a regular file')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        # Check the lock file was not swapped between opening and locking.
        opened=os.fstat(fd);current=os.stat(path,follow_symlinks=False)
        if (opened.st_dev,opened.st_ino)!=(current.st_dev,current.st_ino):
            raise ValueError('Source lock identity changed')
        observed=guard(main,diagnostic,capture,directories)
        yield dict(observed,cooperative_source_lock_held=True,
            diagnostic_launch_locked=False,unreviewed_worker_launch_blocked=False)
    finally:
        os.close(fd)
