"""Synthetic /proc identity protocol, no process launch or GPU acceptance."""
from copy import deepcopy
from pathlib import Path
import pytest
from common.data_contracts import atomic_json,resolve
from train import independent_runtime_contract as gate
from scripts.horizon_probe_process import OWNER_ENV


@pytest.mark.parametrize('fault',['none','tag','launcher_start','supervisor_identity','execution','control'])
def test_launcher_and_supervisor_both_bound(tmp_path,monkeypatch,fault):
    execution=tmp_path/'attempt/execution.json';execution.parent.mkdir()
    supervisor=dict(pid=200,start_ticks=20,argv_sha256='original',cwd=str(resolve('.')),uid=1100,
        argv=['python',str(resolve(gate.SUPERVISOR)),'--control-dir',str(tmp_path)])
    parent=dict(pid=300,ppid=200,start_ticks=30,argv=['python','-m','torch.distributed.run',
        '--standalone','--nproc_per_node=8','--module',gate.MODULE,'--execution',str(execution)])
    attempt=dict(runtime_contract=str(tmp_path/'runtime_contract.json'),supervisor=deepcopy(supervisor))
    atomic_json(execution.parent/'launch/handle.json',dict(pid=300,process=dict(start_ticks=30),owner='b'*32))
    monkeypatch.setenv(OWNER_ENV,'b'*32);monkeypatch.setattr(gate.os,'getppid',lambda:300)
    if fault=='tag':monkeypatch.setenv(OWNER_ENV,'c'*32)
    elif fault=='launcher_start':parent['start_ticks']=31
    elif fault=='supervisor_identity':supervisor['argv_sha256']='changed'
    elif fault=='execution':parent['argv'][-1]=str(tmp_path/'other.json')
    elif fault=='control':supervisor['argv'][-1]=str(tmp_path/'other')
    monkeypatch.setattr('scripts.horizon_process_handoff.process_record',lambda pid:{200:supervisor,300:parent}[pid])
    if fault=='none':
        assert gate.launcher_identity(execution,attempt)==dict(launcher_pid=300,launcher_start_ticks=30,owner='b'*32)
    else:
        with pytest.raises(ValueError):gate.launcher_identity(execution,attempt)
