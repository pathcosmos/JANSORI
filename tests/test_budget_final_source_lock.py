import fcntl
import os
from pathlib import Path
import subprocess
import sys
import pytest
import scripts.budget_final_source_lock as source


@pytest.fixture
def roots(tmp_path,monkeypatch):
    main=tmp_path/'main';main.mkdir();(main/'controller.lock').write_text('')
    monkeypatch.setattr(source,'guard',lambda *a:dict(status='observed_source_diagnostic_and_readers_absent'))
    return main,tmp_path/'diag',[str(tmp_path/'dependency')]


def test_lock_is_held_and_released_after_normal_and_error(roots):
    main,diag,dirs=roots;path=main/'controller.lock'
    for fail in [False,True]:
        try:
            with source.completed_source_lock(main,diag,{},dirs) as result:
                assert result['cooperative_source_lock_held']
                assert not result['diagnostic_launch_locked'] and not result['unreviewed_worker_launch_blocked']
                with path.open('r+') as other:
                    with pytest.raises(BlockingIOError):fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
                if fail:raise RuntimeError('private test')
        except RuntimeError:
            assert fail
        with path.open('r+') as other:fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)


def test_other_real_process_blocks_lock_without_signalling_it(roots):
    main,diag,dirs=roots;path=main/'controller.lock'
    child=subprocess.Popen([sys.executable,'-u','-c',
        'import fcntl,sys,time; f=open(sys.argv[1],"r+"); fcntl.flock(f,fcntl.LOCK_EX); print("ready",flush=True); time.sleep(30)',str(path)],
        stdout=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='ready'
        with pytest.raises(BlockingIOError):
            with source.completed_source_lock(main,diag,{},dirs):pass
        assert child.poll() is None
    finally:
        child.terminate();child.wait(timeout=5);child.stdout.close()


def test_symlink_and_missing_lock_fail_without_creating_file(roots,tmp_path):
    main,diag,dirs=roots;lock=main/'controller.lock';lock.unlink()
    with pytest.raises(FileNotFoundError):
        with source.completed_source_lock(main,diag,{},dirs):pass
    target=tmp_path/'target';target.write_text('');lock.symlink_to(target)
    with pytest.raises(OSError):
        with source.completed_source_lock(main,diag,{},dirs):pass
