from pathlib import Path
import pytest
import scripts.budget_final_isolation_guard as gate


@pytest.fixture
def mocked(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(gate,'require_parent_absent',lambda *a:calls.append('captured'))
    monkeypatch.setattr(gate,'active_source_processes',lambda *a:[])
    monkeypatch.setattr(gate,'running',lambda *a:[])
    monkeypatch.setattr(gate,'no_readers',lambda p:calls.append(str(p)))
    return tmp_path/'main',tmp_path/'diag',[str(tmp_path/'one'),str(tmp_path/'two')],calls


def test_guard_rechecks_processes_after_all_reader_scans(mocked):
    main,diag,dirs,calls=mocked
    result=gate.guard(main,diag,{},dirs)
    assert calls==['captured',*dirs,'captured']
    assert not any(result[k] for k in ['future_launch_locked','access_denied_verified',
        'actual8gpu_verified','retirement_authorized','goal_complete'])


@pytest.mark.parametrize('fault',['source','diagnostic','reader','appeared','overlap','alias','nested'])
def test_unsafe_state_or_scope_fails_closed(mocked,monkeypatch,fault):
    main,diag,dirs,calls=mocked
    if fault=='source':monkeypatch.setattr(gate,'active_source_processes',lambda *a:[123])
    elif fault=='diagnostic':monkeypatch.setattr(gate,'running',lambda *a:[123])
    elif fault=='reader':monkeypatch.setattr(gate,'no_readers',lambda *a:(_ for _ in ()).throw(RuntimeError('reader')))
    elif fault=='appeared':
        states=iter([[],[123]]);monkeypatch.setattr(gate,'running',lambda *a:next(states))
    elif fault=='overlap':dirs=[str(main.parent)]
    elif fault=='alias':dirs=[str(main.parent/'one'),str(main.parent/'one/../one')]
    elif fault=='nested':dirs=[str(main.parent/'one'),str(main.parent/'one/two')]
    with pytest.raises((ValueError,RuntimeError)):gate.guard(main,diag,{},dirs)


def test_real_open_file_blocks_guard_until_private_reader_exits(tmp_path):
    import subprocess,sys
    from scripts.seal_horizon_handoff import ROLES
    main=tmp_path/'main';diag=tmp_path/'diag';directory=tmp_path/'dependency'
    directory.mkdir();payload=directory/'fixture';payload.write_text('small fixture')
    # Synthetic process identities are well outside the Linux PID range; the
    # actual open-file reader below is separately checked through /proc.
    capture={'status':'observed_live_not_stopped','run':str(main),
        'processes':{role:{'pid':100_000_000+i,'start_ticks':1} for i,role in enumerate(sorted(ROLES))}}
    child=subprocess.Popen([sys.executable,'-u','-c',
        'import sys,time; f=open(sys.argv[1]); print("ready",flush=True); time.sleep(30)',str(payload)],
        stdout=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='ready'
        with pytest.raises(RuntimeError,match='active file handle'):
            gate.guard(main,diag,capture,[str(directory)])
    finally:
        child.terminate();child.wait(timeout=5)
        child.stdout.close()
    result=gate.guard(main,diag,capture,[str(directory)])
    assert result['status']=='observed_source_diagnostic_and_readers_absent'
    assert not result['future_launch_locked'] and not result['actual8gpu_verified']
