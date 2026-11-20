"""Read-only observer state/event protocol, synthetic runtime input only."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json

import pytest
from common.data_contracts import sha256_file,resolve
from scripts.tiered_checkpoint_retention import atomic_json
from scripts import observe_independent_training as m


@pytest.fixture
def view(tmp_path):
    main=tmp_path/'main';main.mkdir();(main/'training').mkdir();(main/'checkpoint_certificates').mkdir()
    cfg=dict(max_steps=57000,global_tokens=2097152,log_every=10,eval_every=250,ckpt_every=500)
    rows=[dict(step=s,loss=2.,gnorm=.1,lr=.0003,tok_s=137000.,tokens_b=s*2097152/1e9) for s in [11500,11510]]
    (main/'training/log.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    observation=dict(phase='running',source_state={'phase':'running'},processes={'supervisor':{'pid':1},'launcher':{'pid':2},'all8_consumed_live':True},consumption={'proved':True},completion=None)
    v=SimpleNamespace(main=main,config=cfg,acceptance={'parent_training_contract_id':'original'},observe=lambda:deepcopy(observation),observation=observation)
    return v


@pytest.mark.parametrize('fault',['none','restoring','dead_rank','no_supervisor','source_error','waiting','complete'])
def test_observer_states_distinguish_liveness_consumption_and_completion(view,fault):
    v=view
    if fault=='restoring':v.observation['consumption']=None
    elif fault=='dead_rank':v.observation['processes']['all8_consumed_live']=False
    elif fault=='no_supervisor':v.observation['processes']['supervisor']=None
    elif fault=='source_error':v.observation['phase']='error'
    elif fault=='waiting':v.observation['phase']='await_independent_execution';v.observation['processes']=None
    elif fault=='complete':
        v.observation['completion']={'goal_complete':False}
        row=dict(step=57000,loss=2.,gnorm=.1,lr=.00003,tok_s=137000.,tokens_b=57000*2097152/1e9)
        (v.main/'training/log.jsonl').write_text(json.dumps(row)+'\n')
    out,history,events=m.cycle(v,{'seen':[]},1000)
    expected=dict(none='running',restoring='restoring_await_all8_consumption',dead_rank='unexpected_exit',no_supervisor='unexpected_exit',source_error='error',waiting='await_independent_execution',complete='target_saved_final_review_pending')
    assert out['case']==expected[fault] and (out['phase']=='complete')==(fault=='complete')
    step,observed=(57000,57000) if fault=='complete' else (11500,11510)
    assert any(e['event']=='training_milestone_observed' and e['step']==step and e['observed_log_step']==observed for e in events)


def test_stall_and_event_dedup_do_not_signal_or_restart(view,tmp_path,monkeypatch):
    output=tmp_path/'observer';output.mkdir();global_events=tmp_path/'global_events.jsonl'
    original=m.resolve;monkeypatch.setattr(m,'resolve',lambda p:global_events if p=='logs/pipeline_events.jsonl' else original(p))
    _,history,events=m.cycle(view,{'seen':[]},1000)
    m.record_events(output,view.main,history,events,'test-utc');before=global_events.read_text()
    m.record_events(output,view.main,history,events,'test-utc');assert global_events.read_text()==before
    out,history,events=m.cycle(view,history,2201)
    assert out['case']=='suspected_stall'
    assert not (view.main/'state.json').exists()


def test_exact_output_scope_and_existing_pool(tmp_path):
    main=tmp_path/'main';control=tmp_path/'control';pool=tmp_path/'observer';main.mkdir();control.mkdir();pool.mkdir()
    output=pool/'independent_v1'
    view=SimpleNamespace(main=main,control=control,path=control/'runtime_contract.json',digest='a'*64,
        contract={'shared_storage':{'nfs_pools':{'observer':dict(paths=[str(pool)],budget_bytes=2**30)}}},
        binding={'observer_output':str(output),'code':{m.OWN:sha256_file(resolve(m.OWN))}})
    binding=m.admit_output(view,output)
    assert binding['interval_seconds']==30 and binding['resource_interval_seconds']==600
    with pytest.raises(ValueError):m.admit_output(view,pool)
    view.binding['observer_output']=str(main)
    with pytest.raises(ValueError):m.admit_output(view,main)


def test_missing_runtime_never_creates_observer_namespace(tmp_path):
    control=tmp_path/'control';control.mkdir();output=tmp_path/'observer'
    with pytest.raises(FileNotFoundError):m.observe(control,output)
    assert not output.exists()


def test_final_marker_cannot_override_incomplete_observed_trace(view):
    view.observation['completion']={'goal_complete':False}
    with pytest.raises(ValueError,match='final training step'):m.cycle(view,{'seen':[]},1000)
    view.observation['completion']=None;view.observation['phase']='complete'
    state,_,_=m.cycle(view,{'seen':[]},1000)
    assert state['case']=='completed_waiting_proof' and state['phase']=='running'
