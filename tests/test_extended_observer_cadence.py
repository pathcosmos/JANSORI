import json
from pathlib import Path

import pytest

from scripts import observe_extended_training_v2 as observer


def test_slow_resource_sample_does_not_add_a_poll_interval(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir();out=tmp_path/'observer';training=source/'training';training.mkdir()
    (source/'contract.json').write_text('{}')
    (source/'main.json').write_text(json.dumps({'max_steps':57000,'out_dir':str(training)}))
    (source/'pid').write_text('12345')
    (source/'state.json').write_text(json.dumps({'phase':'running','worker_pid':12346}))
    (source/'checkpoint_certificates').mkdir()
    events=tmp_path/'events.jsonl'
    real_resolve=observer.resolve
    def resolve(value):
        if str(value)=='runs':return tmp_path
        if str(value)=='logs/pipeline_events.jsonl':return events
        return real_resolve(value)
    monkeypatch.setattr(observer,'resolve',resolve)
    monkeypatch.setattr(observer,'artifact_path',lambda p:str(p))
    monkeypatch.setattr(observer,'admit_source',lambda *a:None)
    monkeypatch.setattr(observer,'process_identity',lambda *a:{'pid':12345})
    monkeypatch.setattr(observer,'rows_from_trace',lambda *a:[])
    monkeypatch.setattr(observer,'summarize',lambda *a:dict(logged_step=10010,evaluations={}))
    monkeypatch.setattr(observer,'progress_state',lambda summary,history,now,**kw:('running',dict(history,last_progress_unix=now)))
    clock=[1000000.0];samples=[]
    monkeypatch.setattr(observer.time,'time',lambda:clock[0])
    monkeypatch.setattr(observer.time,'monotonic',lambda:clock[0])
    class Stop(BaseException):pass
    def sleep(seconds):
        clock[0]+=seconds
        if len(samples)>=2:raise Stop()
    monkeypatch.setattr(observer.time,'sleep',sleep)
    def snapshot():
        samples.append(clock[0]);clock[0]+=5
        return {'utc':str(clock[0])}
    monkeypatch.setattr('scripts.report_resource_usage.snapshot',snapshot)
    with pytest.raises(Stop):observer.observe(source,out)
    assert samples==[1000000.,1000600.]
    cursor=json.loads((out/'cursor.json').read_text())
    assert cursor['last_resource_unix']==1000600.
    assert len([json.loads(s) for s in events.read_text().splitlines() if json.loads(s)['event']=='resource_sample'])==2
