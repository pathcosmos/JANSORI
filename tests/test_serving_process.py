"""Real owned CPU server/cleanup tests. No final model or GPU execution."""
import copy
import json
import os
from pathlib import Path
import socket
import sys
import time
from types import SimpleNamespace

import pytest

from inference.serving_process import bind_loopback, listener_record, listener_ready, verify_listener
from scripts import run_final_serving as serving


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = r'''
import importlib.util,json,os,signal,sys,threading,time
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from common.data_contracts import atomic_json
from inference.serving_process import bind_loopback,listener_record
from tokenizers import Tokenizer
root=Path(sys.argv[2]);raw=Tokenizer.from_file('tokenizer/candidate_v1/tokenizer.json')
spec=importlib.util.spec_from_file_location('fixture_helpers','tests/test_serving_validation.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*_):pass
 def send(self,value,status=200,sse=False):
  data=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode()
  self.send_response(status);self.send_header('Content-Type','text/event-stream' if sse else 'application/json')
  self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def do_GET(self):
  self.send(b'' if self.path=='/health' else dict(object='list',data=[dict(id=mod.MODEL)]))
 def do_POST(self):
  p=json.loads(self.rfile.read(int(self.headers['Content-Length'])));prompt=p['prompt']
  ids=raw.encode(prompt,add_special_tokens=False).ids if isinstance(prompt,str) else prompt
  if len(ids)+p['max_tokens']>4096:
   self.send(dict(error=dict(type='BadRequestError',message='maximum context exceeded')),400);return
  r=mod.completion(raw,prompt,maximum=p['max_tokens'],forced='allowed_token_ids' in p)
  if os.environ.get('FIXTURE_WRONG_MODEL'):r['model']='wrong-model'
  if p.get('stream'):
   body=b''.join(b'data: '+json.dumps(x).encode()+b'\n\n' for x in mod.stream_events(r))
   self.send(body+b'data: [DONE]\n\n',sse=True)
  else:self.send(r)
sock=bind_loopback();server=ThreadingHTTPServer(sock.getsockname(),Handler,bind_and_activate=False)
server.socket.close();server.socket=sock;server.server_address=sock.getsockname();server.server_activate()
record=listener_record(sock,owner=os.environ['LLM_HORIZON_DIAGNOSTIC_OWNER'])
atomic_json(root/'server_execution.json',record)
signal.signal(signal.SIGTERM,lambda *_:threading.Thread(target=server.shutdown).start())
server.serve_forever(poll_interval=.01);server.server_close()
atomic_json(root/'server_exit.json',dict(fixture=True))
'''


def config():
    return dict(max_seconds=30,max_pss_gib=2,startup_seconds=10,model_alias='llm-dense-3b-35k',
                tokenizer='tokenizer/candidate_v1/tokenizer.json',prompts='runs/f7_preflight_v1/prompts.jsonl')


def sample(job):
    return dict(memory_used_mib=0,owned_compute_processes=[],fixture=True)


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    script=tmp_path/'fixture.py';script.write_text(FIXTURE)
    root=tmp_path/'run';root.mkdir()
    monkeypatch.setattr(serving,'command',lambda r,c:[sys.executable,str(script),'--run-dir',str(r)])
    return root


def test_owned_cpu_http_lifecycle(launcher):
    calls=[]
    metrics=serving.run_owned_http(launcher,config(),dict(os.environ),SimpleNamespace(check=lambda:None),
        lambda pid,elapsed,peak:calls.append((pid,elapsed,peak)),sample=sample)
    assert metrics['exit_code']==0 and not metrics['remaining_owned_processes']
    assert calls and metrics['peak_pss_bytes']>0
    results=json.loads((launcher/'http_checks.json').read_text())
    assert len(results['requests'])==34 and not results['actual_final3b_execution_verified']
    assert (launcher/'listener_verified.json').exists()
    assert not (launcher/'.done').exists()
    with pytest.raises(RuntimeError,match='Preserve prior'):
        serving.reject_previous(launcher)


def test_wrong_api_preserved_server_stopped(launcher):
    with pytest.raises(ValueError,match='identity'):
        serving.run_owned_http(launcher,config(),dict(os.environ,FIXTURE_WRONG_MODEL='1'),
            SimpleNamespace(check=lambda:None),lambda *_:None,sample=sample)
    result=json.loads((launcher/'server/failure.json').read_text())
    assert result['outputs_preserved']
    assert not Path(f"/proc/{result['processes'][0]['pid']}").exists()
    assert not (launcher/'.done').exists()


def test_resource_exception_cleans_owned_command(launcher):
    def fail(*_):raise RuntimeError('fixture memory budget')
    with pytest.raises(RuntimeError,match='memory budget'):
        serving.run_owned_http(launcher,config(),dict(os.environ),SimpleNamespace(check=lambda:None),fail,sample=sample)
    assert (launcher/'server/failure.json').exists()
    assert not (launcher/'http_checks.json').exists()


def test_exclusive_loopback():
    with bind_loopback() as sock:
        assert sock.getsockname()[0]=='127.0.0.1'
        assert sock.getsockopt(socket.SOL_SOCKET,socket.SO_REUSEPORT)==0
        with socket.socket() as other:
            other.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEPORT,1)
            with pytest.raises(OSError):other.bind(sock.getsockname())
        r=listener_record(sock,owner='test')
        with pytest.raises(ValueError,match='ownership'):
            verify_listener(r,dict(pid=r['pid']+1,process=dict(start_ticks=r['start_ticks']),owner='test',command=[]),[])


def test_process_and_socket_substitution_rejected(launcher):
    from scripts.horizon_probe_process import OwnedCommand
    job=OwnedCommand(serving.command(launcher,config()),launcher,dict(os.environ))
    # OwnedCommand requires an empty directory: use the run itself only here.
    try:
        until=time.monotonic()+10
        while not (launcher/'server_execution.json').exists():
            if job.proc.poll() is not None or time.monotonic()>until:raise RuntimeError('Fixture startup failed')
            time.sleep(.02)
        record=json.loads((launcher/'server_execution.json').read_text())
        handle=json.loads((launcher/'handle.json').read_text())
        cmd=serving.command(launcher,config())
        assert listener_ready(record,handle,cmd)
        for key,value in [('pid',record['pid']+1),('start_ticks',record['start_ticks']+1),
                          ('inode',record['inode']+1),('port',record['port']+1),('owner','other'),('host','0.0.0.0')]:
            wrong={**record,key:value}
            with pytest.raises((ValueError,FileNotFoundError)):
                verify_listener(wrong,handle,cmd)
    finally:
        job.cleanup();job.close()


def test_actual_dependency_live_and_incomplete_source_gate():
    c=serving.make_contract('artifacts/3b_base_35k_v1/vllm_runtime_v1')
    assert serving.dependency_ready(c) is False
    with pytest.raises(ValueError,match='not complete'):
        serving.authenticate_sources(c)
    assert c['purpose']=='final35k_vllm_serving' and c['model_alias']=='llm-dense-3b-35k'


def test_controller_error_does_not_publish(launcher,monkeypatch):
    monkeypatch.setattr(serving,'dependency_ready',lambda c:True)
    def fail(c):raise ValueError('source not certified')
    monkeypatch.setattr(serving,'authenticate_sources',fail)
    with pytest.raises(ValueError,match='not certified'):
        serving.controller(launcher,dict(wait_seconds=1,poll_seconds=.01))
    assert json.loads((launcher/'state.json').read_text())['phase']=='error'
    assert not (launcher/'.done').exists() and not (launcher/'server').exists()


def test_independent_final_review_with_synthetic_gpu_source(launcher,monkeypatch):
    """Real CPU HTTP + synthesized final/GPU receipts; NOT actual3B proof."""
    from common.data_contracts import identity,sha256_file,atomic_json
    from inference.vllm_server import server_arguments
    c=serving.make_contract('artifacts/3b_base_35k_v1/vllm_runtime_v1');c.update(config())
    def fake_sample(job):
        return dict(unix=time.time(),gpu_index=0,uuid='SYNTHETIC',name='H200 SYNTHETIC',
            memory_used_mib=100,memory_total_mib=140000,utilization_percent=1,
            owned_compute_processes=[dict(pid=job.proc.pid,memory_mib=100)])
    metrics=serving.run_owned_http(launcher,c,dict(os.environ),SimpleNamespace(check=lambda:None),
        lambda *_:None,sample=fake_sample)
    admitted=dict(contract_id=identity(c),source_binding=dict(step=35000,synthetic=True),manifest_sha256='a'*64)
    atomic_json(launcher/'admitted.json',admitted)
    monkeypatch.setattr(serving,'authenticate_sources',lambda _:admitted)
    p=launcher/'server_execution.json';execution=json.loads(p.read_text())
    execution.update(contract_id=identity(c),admission_sha256=sha256_file(launcher/'admitted.json'),
        manifest_sha256=admitted['manifest_sha256'],versions=c['versions'],visible_device_count=1,
        cuda_build='12.8',device_name='H200 SYNTHETIC',argv=server_arguments(c))
    atomic_json(p,execution)
    for name in ['listener_verified.json','http_complete.json']:
        p=launcher/name;d=json.loads(p.read_text());d['server_execution_sha256']=sha256_file(launcher/'server_execution.json');atomic_json(p,d)
    atomic_json(launcher/'server_exit.json',dict(status='server_context_exited',contract_id=identity(c),
        execution_sha256=sha256_file(launcher/'server_execution.json'),seconds=.5))
    atomic_json(launcher/'resource_peak.json',dict(combined_peak_pss_bytes=metrics['peak_pss_bytes']+1024))
    result=serving.verify_result(launcher,c)
    assert result['status']=='passed_actual_final35k_vllm_http' and result['source_binding']['synthetic']
    assert not result['goal_complete'] and not (launcher/'.done').exists()
    corruptions=[
        ('server_execution.json',lambda d:d.update(pid=d['pid']+1)),
        ('server/result.json',lambda d:d.update(remaining_owned_processes=[999])),
        ('server/result.json',lambda d:d.update(elapsed_seconds=float('nan'))),
        ('listener_verified.json',lambda d:d.update(port=d['port']+1)),
        ('server_exit.json',lambda d:d.update(execution_sha256='b'*64)),
        ('http_complete.json',lambda d:d.update(checks_sha256='b'*64)),
        ('resource_peak.json',lambda d:d.update(combined_peak_pss_bytes=2**60)),
        ('gpu_samples.json',lambda d:d['samples'][0]['owned_compute_processes'][0].update(pid=1)),
        ('gpu_samples.json',lambda d:d.update(peak_observed_mib=999)),
    ]
    for name,change in corruptions:
        p=launcher/name;original=p.read_bytes();d=json.loads(original);change(d);atomic_json(p,d)
        try:
            with pytest.raises(ValueError):serving.verify_result(launcher,c)
        finally:p.write_bytes(original)
    p=launcher/'http_checks.json';original=p.read_bytes();done=launcher/'http_complete.json';done_original=done.read_bytes()
    d=json.loads(original);d['requests'][0]['request']['ignore_eos']=True;atomic_json(p,d)
    done_value=json.loads(done_original);done_value['checks_sha256']=sha256_file(p);atomic_json(done,done_value)
    try:
        with pytest.raises(ValueError,match='request suite'):serving.verify_result(launcher,c)
    finally:p.write_bytes(original);done.write_bytes(done_original)
