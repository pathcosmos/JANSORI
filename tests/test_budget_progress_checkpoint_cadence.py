"""Reproduce transition timeout with a live producer whose ledgers flush on save.

No real process, signal, sleep, GPU or production write is used.
"""
import json
import pytest
from scripts.run_budget_transition import wait_progress
import scripts.run_budget_transition as controller
import scripts.capture_budget_producer as capture_module
import train.budget_horizon_handoff as handoff_module


def source(tmp_path,monkeypatch):
    main=tmp_path/'main';(main/'training').mkdir(parents=True)
    (main/'ledgers').mkdir()
    def write(name,value): (main/name).write_text(json.dumps(value))
    write('contract.json',{'budget_execution':{'parent_step':16000}})
    write('main.json',{'max_steps':50000,'ckpt_every':500})
    write('state.json',{'phase':'running','worker_pid':20})
    write('training/training_contract.json',{'id':'same-production-contract'})
    write('training/log.jsonl',{'step':16020,'loss':2.3,'tok_s':136228})
    monkeypatch.setattr(handoff_module,'load_handoff',lambda *a:({}, {'id':'same-production-contract'}, {}))
    monkeypatch.setattr(controller,'children',lambda p:list(range(21,29)))
    monkeypatch.setattr(capture_module,'capture_budget_producer',lambda *a:{'attempt':'main-live'})
    clock=iter([0,1201]);monkeypatch.setattr(controller.time,'monotonic',lambda:next(clock))
    monkeypatch.setattr(controller.time,'sleep',lambda s:None)
    return main,write


def test_live_training_log_cannot_satisfy_current_precheckpoint_gate(tmp_path,monkeypatch):
    main,write=source(tmp_path,monkeypatch)
    with pytest.raises(TimeoutError,match='never relaunch automatically'):
        wait_progress(main,10,deadline=1200,heartbeat=lambda:None)
    assert json.loads((main/'training/log.jsonl').read_text())['step']==16020
    assert not list((main/'ledgers').glob('*.json'))


def test_first_checkpoint_journal_allows_exact_first8rank_verification(tmp_path,monkeypatch):
    main,write=source(tmp_path,monkeypatch)
    for rank in range(8):
        write(f'ledgers/main-live-rank{rank}.json',dict(rank=rank,contract_id='same-production-contract',
            steps=[dict(step=s,source_sequences={'ko':64}) for s in range(16001,16501)]))
    result=wait_progress(main,10,deadline=1200,heartbeat=lambda:None)
    assert result['committed_step']==16001
    assert result['committed_tokens_added']==2097152
    assert [r['rank'] for r in result['ranks']]==list(range(8))
