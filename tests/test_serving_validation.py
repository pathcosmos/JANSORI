"""HTTP fixture tests, never actual vLLM/model execution proof."""
from contextlib import contextmanager
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time

import pytest
from tokenizers import Tokenizer

from inference.serving_validation import (Events, LocalClient, assemble_stream,
    check_completion, run_checks, strict_json)


ROOT = Path(__file__).resolve().parents[1]
MODEL = 'llm-dense-3b-35k'


@pytest.fixture(scope='module')
def raw():
    return Tokenizer.from_file(str(ROOT/'tokenizer/candidate_v1/tokenizer.json'))


def completion(raw, prompt, *, ids=None, maximum=4, forced=False):
    source = raw.encode(prompt, add_special_tokens=False).ids if isinstance(prompt, str) else prompt
    ids = ids if ids is not None else ([1] if forced else (raw.encode('A').ids*maximum)[:maximum])
    lp = dict(tokens=[f'token_id:{t}' for t in ids], token_logprobs=[-.5]*len(ids),
              top_logprobs=[{f'token_id:{t}':-.5} for t in ids], text_offset=list(range(len(ids))))
    return dict(id='cmpl-fixture', object='text_completion', created=1, model=MODEL,
        choices=[dict(index=0, text=raw.decode(ids, skip_special_tokens=True), token_ids=ids,
            prompt_token_ids=source, finish_reason='stop' if forced else 'length', stop_reason=None, logprobs=lp)],
        usage=dict(prompt_tokens=len(source), completion_tokens=len(ids), total_tokens=len(source)+len(ids)))


def stream_events(obj):
    # Empty leading chunk followed by token-bearing completion and usage.
    first = copy.deepcopy(obj)
    first.pop('usage')
    first['choices'][0].update(text='', token_ids=[], finish_reason=None,
                              logprobs={k:[] for k in first['choices'][0]['logprobs']})
    second = copy.deepcopy(obj)
    second.pop('usage')
    second['choices'][0]['prompt_token_ids'] = None
    last = {**{k:obj[k] for k in ['id', 'object', 'created', 'model']}, 'choices':[], 'usage':obj['usage']}
    return [first, second, last]


@contextmanager
def server(raw, *, change=None, delay=0, oversized=False, chunked=False, truncated=False):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_):
            pass

        def send(self, body, status=200, media='application/json'):
            self.send_response(status)
            self.send_header('Content-Type', media)
            if chunked:
                self.send_header('Transfer-Encoding', 'chunked')
            else:
                self.send_header('Content-Length', str(len(body)+(10 if truncated else 0)))
            self.end_headers()
            if delay:
                time.sleep(delay)
            try:
                if chunked:
                    for at in range(0, len(body), 7):
                        part = body[at:at+7]
                        self.wfile.write(f'{len(part):x}\r\n'.encode()+part+b'\r\n')
                    self.wfile.write(b'0\r\n\r\n')
                else:
                    self.wfile.write(body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if self.path == '/health':
                self.send(b'', media='text/plain')
            else:
                self.send(json.dumps(dict(object='list', data=[dict(id=MODEL)])).encode())

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(payload)
            prompt = payload['prompt']
            ids = raw.encode(prompt, add_special_tokens=False).ids if isinstance(prompt, str) else prompt
            if len(ids)+payload['max_tokens'] > 4096:
                self.send(json.dumps(dict(error=dict(type='BadRequestError', message='maximum context exceeded'))).encode(), 400)
                return
            obj = completion(raw, prompt, maximum=payload['max_tokens'], forced='allowed_token_ids' in payload)
            if change:
                change(obj)
            if oversized:
                self.send(b'x'*1000)
            elif payload.get('stream'):
                body = b''.join(b'data: '+json.dumps(o, ensure_ascii=False).encode()+b'\n\n' for o in stream_events(obj))
                self.send(body+b'data: [DONE]\n\n', media='text/event-stream')
            else:
                self.send(json.dumps(obj, ensure_ascii=False).encode())

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={'poll_interval':.01}, daemon=True)
    thread.start()
    try:
        yield httpd.server_port, calls
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(2)


def test_real_http_fixture_full_suite(raw):
    prompts = [json.loads(l) for l in (ROOT/'runs/f7_preflight_v1/prompts.jsonl').read_text().splitlines() if l.strip()]
    guarded = []
    with server(raw) as (port, calls):
        result = run_checks(LocalClient(port, guard=lambda:guarded.append(True)), raw, prompts)
    assert len(calls) == 34
    assert len(result['results']) == 12
    assert len(result['requests']) == 34
    assert len(guarded) > len(calls)
    assert not result['actual_final3b_execution_verified']
    assert not result['server_ownership_verified']
    assert not result['vram_peak_measured']
    assert not result['goal_complete']
    assert all(r['request']['ignore_eos'] is False for r in result['requests'][:30])
    assert result['results'][-1]['stream']['finish_reason'] == 'stop'


def test_byte_fragmented_unicode_sse():
    p = Events()
    wire = b'data: '+json.dumps(dict(choices=[dict(token_ids=[10], text='한글😀')]), ensure_ascii=False).encode()+b'\r\n\r\ndata: [DONE]\r\n\r\n'
    for i, byte in enumerate(wire):
        p.feed(bytes([byte]), i/1000)
    assert p.finish()[0]['choices'][0]['text'] == '한글😀'
    assert p.first_token_seconds is not None


def test_actual_chunked_http_and_truncation(raw):
    with server(raw, chunked=True) as (port, _):
        reply = LocalClient(port, guard=lambda:None).request('/v1/completions',
            dict(prompt='x', max_tokens=4, stream=True), stream=True)
        obj = assemble_stream(reply, MODEL)
        assert check_completion(obj, dict(id='x', ids=raw.encode('x').ids, generated=4), raw, MODEL)
    with server(raw, truncated=True) as (port, _):
        with pytest.raises(ValueError, match='Truncated HTTP body'):
            LocalClient(port, guard=lambda:None).request('/v1/models')


@pytest.mark.parametrize('wire', [
    b'data: {}\n\n',
    b'data: {}\n\ndata: [DONE]',
    b'data: {}\n\ndata: [DONE]\n\ndata: {}\n\n',
    b'data: {"error":"bad"}\n\ndata: [DONE]\n\n',
    b'data: {}\ndata: {}\n\ndata: [DONE]\n\n',
    b'event: message\ndata: {}\n\n',
    b'data: '+b'x'*65537,
])
def test_bad_stream_rejected(wire):
    with pytest.raises(ValueError):
        p = Events(); p.feed(wire, .1); p.finish()


@pytest.mark.parametrize('value', ['{"x":NaN}', '{"x":Infinity}', '{"x":1,"x":2}'])
def test_ambiguous_json_rejected(value):
    with pytest.raises(ValueError):
        strict_json(value)


def test_client_budget_timeout_and_guard(raw):
    with server(raw, oversized=True) as (port, _):
        with pytest.raises(ValueError, match='byte budget'):
            LocalClient(port, guard=lambda:None, max_bytes=50).request('/v1/completions', dict(prompt='x', max_tokens=1))
    with server(raw, delay=.1) as (port, _):
        with pytest.raises(TimeoutError):
            LocalClient(port, guard=lambda:None, timeout=.02).request('/v1/models')
    def fail():
        raise RuntimeError('source changed')
    with pytest.raises(RuntimeError, match='source changed'):
        LocalClient(1, guard=fail).request('/health')
    for port in ['remote:80', 0, True, 65536]:
        with pytest.raises(ValueError):
            LocalClient(port, guard=lambda:None)


@pytest.mark.parametrize('mutation', [
    lambda r:r.update(model='wrong'),
    lambda r:r['usage'].update(prompt_tokens=999),
    lambda r:r['choices'][0].update(prompt_token_ids=[99]),
    lambda r:r['choices'][0].update(token_ids=[65536]*4),
    lambda r:r['choices'][0].update(text='wrong'),
    lambda r:r['choices'][0].update(finish_reason='stop'),
    lambda r:r['choices'][0]['logprobs'].update(token_logprobs=[float('nan')]*4),
    lambda r:r['choices'][0]['logprobs'].update(tokens=['token_id:999']*4),
])
def test_corrupt_completion_rejected(raw, mutation):
    obj = completion(raw, 'x'); mutation(obj)
    with pytest.raises(ValueError):
        check_completion(dict(status=200, seconds=.1, body=obj),
                         dict(id='x', ids=raw.encode('x').ids, generated=4), raw, MODEL)


@pytest.mark.parametrize('mutation', [
    lambda e:e.pop(),
    lambda e:e[1].update(id='different'),
    lambda e:e.append(copy.deepcopy(e[1])),
    lambda e:e[1]['choices'][0].update(prompt_token_ids=[99]),
    lambda e:e[0].update(usage={}),
])
def test_corrupt_stream_order_rejected(raw, mutation):
    events = stream_events(completion(raw, 'x')); mutation(events)
    with pytest.raises(ValueError):
        assemble_stream(dict(status=200, seconds=.1, events=events), MODEL)


def test_eos_is_not_length_or_natural_quality_proof(raw):
    case = dict(id='forced', ids=raw.encode('x').ids, generated=4)
    obj = completion(raw, 'x', forced=True)
    reply = dict(status=200, seconds=.1, body=obj)
    assert check_completion(reply, case, raw, MODEL, mode='forced_eos')['token_ids'] == [1]
    obj['choices'][0]['finish_reason'] = 'length'
    with pytest.raises(ValueError):
        check_completion(reply, case, raw, MODEL, mode='forced_eos')


@pytest.mark.parametrize('seconds', [0, float('nan'), -1])
def test_invalid_timing_not_throughput(raw, seconds):
    with pytest.raises(ValueError, match='timing'):
        check_completion(dict(status=200, seconds=seconds, body=completion(raw, 'x')),
                         dict(id='x', ids=raw.encode('x').ids, generated=4), raw, MODEL)
