from copy import deepcopy
from pathlib import Path
import json
import pytest
from common.data_contracts import sha256_file
import scripts.launch_budget_observer_v2 as launcher


@pytest.fixture
def plan():
    return json.loads(Path(launcher.PLAN).read_text())


def test_exact_reviewed_plan_has_one_readonly_observer_command(plan):
    cmd=launcher.validate_plan(plan,sha256_file(Path('scripts/observe_budget_training_v2.py')))
    assert cmd.count('--source-run')==1 and cmd.count('--run-dir')==1
    assert 'checkpoint_observer_v2' in cmd[-1]


@pytest.mark.parametrize('fault',['script','root','command','memory','metadata','gpu','authorization','restart'])
def test_changed_scope_budget_or_evidence_rejected(plan,fault):
    p=deepcopy(plan);digest=sha256_file(Path('scripts/observe_budget_training_v2.py'))
    if fault=='script':digest='a'*64
    elif fault=='root':p['output']='runs/unreviewed'
    elif fault=='command':p['command'].append('--run')
    elif fault=='memory':p['caps']['rss_mib']=16384
    elif fault=='metadata':p['caps']['metadata_mib']=1024
    elif fault=='gpu':p['caps']['gpu_visible']=True
    elif fault=='authorization':p['checkpoint_deletion_authorized']=True
    elif fault=='restart':p['training_restart_authorized']=True
    with pytest.raises(ValueError):launcher.validate_plan(p,digest)


def test_start_without_explicit_approval_does_not_check_or_launch(monkeypatch,tmp_path):
    monkeypatch.setattr(launcher,'APPROVAL',str(tmp_path/'absent.json'))
    monkeypatch.setattr(launcher,'check',lambda:pytest.fail('Unexpected admission after absent approval'))
    monkeypatch.setattr(launcher,'launch_once',lambda *a:pytest.fail('Unexpected launch'))
    with pytest.raises(FileNotFoundError):launcher.start()
