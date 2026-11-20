"""Bounded localhost completion checks; no server launch or model certification.

The production caller must separately authenticate the final package, own the
server process/socket, admit resources and supervise its lifetime. A successful
response from an arbitrary port does not prove final3B execution.
"""
import http.client
import json
import math
import time

from inference.vllm_validation import build_cases


def strict_json(data):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError('Duplicate JSON key')
            out[key] = value
        return out

    def constant(_):
        raise ValueError('Nonfinite JSON constant')

    return json.loads(data, object_pairs_hook=unique, parse_constant=constant)


class Events:
    """Parse the pinned vLLM single-data-line SSE protocol, including EOF."""
    def __init__(self):
        self.pending = b''
        self.frame = []
        self.events = []
        self.done = False
        self.first_token_seconds = None

    def feed(self, block, elapsed):
        self.pending += block
        while b'\n' in self.pending:
            line, self.pending = self.pending.split(b'\n', 1)
            if len(line) > 65536:
                raise ValueError('SSE line exceeds budget')
            line = line.removesuffix(b'\r')
            if self.done:
                if line:
                    raise ValueError('SSE data after DONE')
                continue
            if line:
                if not line.startswith(b'data: '):
                    raise ValueError('Unexpected SSE field')
                self.frame.append(line[6:])
                continue
            if not self.frame:
                continue
            if len(self.frame) != 1:
                raise ValueError('Unexpected multiline SSE frame')
            value = self.frame.pop()
            if value == b'[DONE]':
                self.done = True
                continue
            obj = strict_json(value)
            if not isinstance(obj, dict) or 'error' in obj:
                raise ValueError('SSE error or invalid object')
            self.events.append(obj)
            if self.first_token_seconds is None:
                if any(c.get('token_ids') for c in obj.get('choices', [])):
                    self.first_token_seconds = elapsed
        if len(self.pending) > 65536:
            raise ValueError('SSE line exceeds budget')

    def finish(self):
        if not self.done or self.frame or self.pending.strip() or not self.events:
            raise ValueError('Incomplete SSE stream')
        return self.events


class LocalClient:
    def __init__(self, port, *, guard, timeout=60, max_bytes=2*1024*1024):
        if type(port) is not int or not 1 <= port <= 65535 or not callable(guard):
            raise ValueError('Explicit local port and live guard required')
        if not 0 < timeout <= 120 or type(max_bytes) is not int or not 1 <= max_bytes <= 4*1024*1024:
            raise ValueError('Invalid HTTP budget')
        self.port, self.guard = port, guard
        self.timeout, self.max_bytes = timeout, max_bytes

    def request(self, path, payload=None, *, stream=False):
        if path not in ('/health', '/v1/models', '/v1/completions'):
            raise ValueError('Unreviewed API path')
        if stream and (not isinstance(payload, dict) or payload.get('stream') is not True):
            raise ValueError('Stream request mismatch')
        self.guard()
        started = time.monotonic()
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=self.timeout)
        parser = Events() if stream else None
        body = bytearray()
        try:
            data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
            if data is not None and len(data) > 128*1024:
                raise ValueError('Request exceeds budget')
            connection.request('GET' if payload is None else 'POST', path, body=data,
                               headers={'Content-Type': 'application/json', 'Connection': 'close'})
            response = connection.getresponse()
            media = response.getheader('Content-Type', '').split(';')[0].strip().lower()
            if 300 <= response.status < 400:
                raise ValueError('API redirects are forbidden')
            if stream and (response.status != 200 or media != 'text/event-stream'):
                raise ValueError('Expected a successful SSE response')
            while True:
                self.guard()
                remaining = self.timeout - (time.monotonic()-started)
                if remaining <= 0:
                    raise TimeoutError('HTTP total deadline exceeded')
                if response.isclosed():
                    break
                # HTTPResponse owns the socket after a Connection: close reply.
                response.fp.raw._sock.settimeout(remaining)
                block = response.read1(min(8192, self.max_bytes-len(body)+1))
                if not block:
                    break
                body.extend(block)
                if len(body) > self.max_bytes:
                    raise ValueError('Response exceeds byte budget')
                if parser is not None:
                    parser.feed(block, time.monotonic()-started)
            elapsed = time.monotonic()-started
            if elapsed > self.timeout:
                raise TimeoutError('HTTP total deadline exceeded')
            if response.length not in (None, 0):
                raise ValueError('Truncated HTTP body')
            self.guard()
            result = dict(status=response.status, seconds=elapsed, bytes=len(body))
            if parser is not None:
                result.update(events=parser.finish(), first_token_seconds=parser.first_token_seconds)
            elif path == '/health' and not body:
                result['body'] = None
            else:
                if media != 'application/json':
                    raise ValueError('Expected a JSON response')
                result['body'] = strict_json(body)
            return result
        finally:
            connection.close()


def integer(n, *, minimum=0):
    if type(n) is not int or n < minimum:
        raise ValueError('Invalid integer count')
    return n


def usage(value, prompt, generated):
    if not isinstance(value, dict) or any(
            integer(value[k]) != n for k, n in
            [('prompt_tokens', prompt), ('completion_tokens', generated), ('total_tokens', prompt+generated)]):
        raise ValueError('Usage count differs')


def header(obj, model):
    if (not isinstance(obj, dict) or obj.get('object') != 'text_completion'
            or obj.get('model') != model or not isinstance(obj.get('id'), str) or not obj['id']):
        raise ValueError('Completion identity differs')
    integer(obj['created'])


def logprobs(choice, ids):
    lp = choice['logprobs']
    if not isinstance(lp, dict) or any(len(lp[k]) != len(ids) for k in
                                     ['tokens', 'token_logprobs', 'top_logprobs', 'text_offset']):
        raise ValueError('Logprob position coverage differs')
    if lp['tokens'] != [f'token_id:{t}' for t in ids]:
        raise ValueError('Logprob token IDs differ')
    for token, value, top in zip(lp['tokens'], lp['token_logprobs'], lp['top_logprobs']):
        if (type(value) not in (int, float) or not math.isfinite(value) or value > 1e-6
                or not isinstance(top, dict) or token not in top or top[token] != value
                or any(type(v) not in (int, float) or not math.isfinite(v) or v > 1e-6 for v in top.values())):
            raise ValueError('Nonfinite or mismatched selected log probability')
    for offset in lp['text_offset']:
        integer(offset)


def check_completion(reply, case, raw, model, *, eos_id=1, mode='normal'):
    if mode not in ('normal', 'length', 'forced_eos'):
        raise ValueError('Unreviewed termination mode')
    seconds = reply['seconds']
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('Invalid request timing')
    if reply['status'] != 200:
        raise ValueError('Completion HTTP failure')
    obj = reply['body']
    header(obj, model)
    if len(obj['choices']) != 1:
        raise ValueError('Require a single completion')
    choice = obj['choices'][0]
    ids = choice['token_ids']
    if (choice['index'] != 0 or type(choice['index']) is not int
            or choice['prompt_token_ids'] != case['ids'] or not isinstance(ids, list)
            or not 1 <= len(ids) <= case['generated']
            or any(type(t) is not int or not 0 <= t < raw.get_vocab_size(with_added_tokens=True) for t in ids)):
        raise ValueError('Prompt/generated token coverage differs')
    reason, stop = choice['finish_reason'], choice.get('stop_reason')
    if reason == 'length':
        if len(ids) != case['generated'] or stop is not None:
            raise ValueError('Length termination differs')
        if mode != 'length' and eos_id in ids:
            raise ValueError('EOS did not terminate generation')
    elif reason == 'stop':
        if mode == 'length' or ids[-1] != eos_id or eos_id in ids[:-1] or stop is not None:
            raise ValueError('EOS termination differs')
    else:
        raise ValueError('Unrecognized completion finish reason')
    if mode == 'forced_eos' and (ids != [eos_id] or reason != 'stop'):
        raise ValueError('Constrained EOS diagnostic failed')
    if choice['text'] != raw.decode(ids, skip_special_tokens=True):
        raise ValueError('Response text differs from original tokenizer')
    logprobs(choice, ids)
    usage(obj['usage'], len(case['ids']), len(ids))
    return dict(id=case['id'], token_ids=ids, text=choice['text'], finish_reason=reason,
                seconds=reply['seconds'], generated_tokens_per_second=len(ids)/reply['seconds'],
                rate_scope='single_request_end_to_end_including_prefill_http')


def assemble_stream(reply, model):
    if reply['status'] != 200:
        raise ValueError('Stream HTTP failure')
    events = reply['events']
    combined = None
    final_usage = None
    terminal = False
    for event in events:
        header(event, model)
        if combined is None:
            combined = dict(id=event['id'], created=event['created'], object='text_completion', model=model,
                choices=[dict(index=0, text='', token_ids=[], prompt_token_ids=None,
                    finish_reason=None, stop_reason=None,
                    logprobs={k:[] for k in ['tokens', 'token_logprobs', 'top_logprobs', 'text_offset']})])
        if event['id'] != combined['id'] or event['created'] != combined['created']:
            raise ValueError('Stream identity changed')
        if not event['choices']:
            if not terminal or final_usage is not None or event.get('usage') is None:
                raise ValueError('Unexpected stream usage event')
            final_usage = event['usage']
            continue
        if terminal or final_usage is not None or len(event['choices']) != 1 or event.get('usage') is not None:
            raise ValueError('Stream ordering/coverage differs')
        row, dest = event['choices'][0], combined['choices'][0]
        if type(row['index']) is not int or row['index'] != 0 or not isinstance(row['token_ids'], list):
            raise ValueError('Stream choice differs')
        if row.get('prompt_token_ids') is not None:
            if dest['prompt_token_ids'] is not None:
                raise ValueError('Repeated stream prompt IDs')
            dest['prompt_token_ids'] = row['prompt_token_ids']
        logprobs(row, row['token_ids'])
        dest['token_ids'].extend(row['token_ids'])
        dest['text'] += row['text']
        for key in dest['logprobs']:
            dest['logprobs'][key].extend(row['logprobs'][key])
        if row.get('finish_reason') is not None:
            dest['finish_reason'] = row['finish_reason']
            dest['stop_reason'] = row.get('stop_reason')
            terminal = True
        elif row.get('stop_reason') is not None:
            raise ValueError('Early stream stop reason')
    if not terminal or final_usage is None:
        raise ValueError('Missing terminal or final usage event')
    combined['usage'] = final_usage
    return dict(status=200, body=combined, seconds=reply['seconds'])


def completion_request(case, prompt, *, model, stream=False, mode='normal'):
    if mode not in ('normal', 'length', 'forced_eos'):
        raise ValueError('Unreviewed termination mode')
    payload = dict(model=model, prompt=prompt, n=1, max_tokens=case['generated'], temperature=0,
        seed=0, add_special_tokens=False, return_token_ids=True, return_tokens_as_token_ids=True,
        logprobs=1, ignore_eos=False, skip_special_tokens=True, spaces_between_special_tokens=False,
        stream=stream)
    if stream:
        payload['stream_options'] = dict(include_usage=True, continuous_usage_stats=False)
    if mode == 'forced_eos':
        payload['allowed_token_ids'] = [1]
    if mode == 'length':
        payload.update(ignore_eos=True, min_tokens=case['generated'])
    return payload


def run_checks(client, raw, prompts, *, model='llm-dense-3b-35k', context=4096):
    """HTTP output evidence only. Ownership/package/GPU provenance is external."""
    health = client.request('/health')
    if health['status'] != 200:
        raise ValueError('Server health failed')
    models = client.request('/v1/models')
    if (models['status'] != 200 or models['body'].get('object') != 'list'
            or [m['id'] for m in models['body']['data']] != [model]):
        raise ValueError('Served model alias differs')
    if raw.token_to_id('<|eos|>') != 1:
        raise ValueError('Original EOS identity differs')
    cases = build_cases(raw, prompts, context=context, generated=16)
    evidence, results = [], []

    def call(case, prompt, *, stream=False, mode='normal'):
        payload = completion_request(case, prompt, model=model, stream=stream, mode=mode)
        reply = client.request('/v1/completions', payload, stream=stream)
        evidence.append(dict(case=case['id'], mode=mode, request=payload, reply=reply))
        normalized = assemble_stream(reply, model) if stream else reply
        checked = check_completion(normalized, case, raw, model, mode=mode)
        if stream:
            first = reply['first_token_seconds']
            if type(first) not in (int, float) or not 0 <= first <= reply['seconds']:
                raise ValueError('Missing first streamed token timing')
            checked['first_token_seconds'] = first
        return checked

    for case in cases[:10]:
        token = call(case, case['ids'])
        text = call(case, case['text'])
        stream = call(case, case['text'], stream=True)
        if any(r['token_ids'] != token['token_ids'] or r['text'] != token['text'] for r in [text, stream]):
            raise ValueError('Token/text/stream greedy results differ')
        results.append(dict(id=case['id'], token=token, text=text, stream=stream))
    boundary = cases[-1]
    results.append(dict(id='context_boundary', result=call(boundary, boundary['ids'], mode='length')))
    forced = dict(id='forced_eos', ids=cases[0]['ids'], generated=4)
    results.append(dict(id='forced_eos', nonstream=call(forced, forced['ids'], mode='forced_eos'),
                        stream=call(forced, forced['ids'], stream=True, mode='forced_eos')))
    overflow_payload = dict(model=model, prompt=boundary['ids']+[boundary['ids'][0]],
        max_tokens=1, temperature=0, add_special_tokens=False)
    overflow = client.request('/v1/completions', overflow_payload)
    error = overflow.get('body', {}).get('error', {})
    if (overflow['status'] != 400 or not isinstance(error, dict)
            or error.get('type') != 'BadRequestError' or not isinstance(error.get('message'), str)
            or not any(term in error['message'].lower() for term in ['context', 'max_model_len', 'maximum'])):
        raise ValueError('Context overflow was not explicitly rejected')
    evidence.append(dict(case='context_overflow', request=overflow_payload, reply=overflow))
    return dict(status='passed_http_output_checks_provenance_required', results=results, requests=evidence,
        health=health, models=models,
        forced_eos_is_diagnostic_not_natural_generation=True, server_ownership_verified=False,
        actual_final3b_execution_verified=False, vram_peak_measured=False, goal_complete=False)
