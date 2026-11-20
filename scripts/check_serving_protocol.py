"""CPU-only pinned vLLM protocol compatibility, not server/model execution."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve, sha256_file, atomic_json
from inference.serving_validation import (Events, assemble_stream, check_completion,
    completion_request)
from inference.vllm_validation import build_cases


def main(output):
    if os.environ.get('CUDA_VISIBLE_DEVICES') != '' or importlib.metadata.version('vllm') != '0.11.2':
        raise ValueError('Require pinned vLLM and hidden CUDA')
    from tokenizers import Tokenizer
    from vllm.entrypoints.openai import protocol
    from vllm.entrypoints.openai import api_server
    from vllm.entrypoints.openai.cli_args import make_arg_parser, validate_parsed_serve_args
    from vllm.utils.argparse_utils import FlexibleArgumentParser
    from inference.vllm_server import server_arguments
    import torch
    if torch.cuda.is_initialized():
        raise ValueError('Unexpected CUDA initialization')
    output = resolve(output)
    if output.exists():
        raise ValueError('Preserve earlier probe output')
    started = time.monotonic()
    raw = Tokenizer.from_file(str(resolve('tokenizer/candidate_v1/tokenizer.json')))
    prompts = [json.loads(l) for l in resolve('runs/f7_preflight_v1/prompts.jsonl').read_text().splitlines() if l.strip()]
    cases = build_cases(raw, prompts, generated=16)
    model = 'llm-dense-3b-35k'
    argv = server_arguments(dict(package='artifacts/3b_base_35k_v1/hf',model_alias=model))
    args = make_arg_parser(FlexibleArgumentParser()).parse_args(argv)
    validate_parsed_serve_args(args); api_server.validate_api_server_args(args)
    expected = dict(host='127.0.0.1',port=0,dtype='bfloat16',max_model_len=4096,
        max_num_seqs=12,max_num_batched_tokens=4096,gpu_memory_utilization=.25,
        tensor_parallel_size=1,enforce_eager=True,enable_prefix_caching=False,
        generation_config='vllm',enable_log_requests=False)
    if any(getattr(args,k)!=v for k,v in expected.items()):
        raise ValueError('Actual server CLI mapping differs')
    requests = []
    for case in cases[:10]:
        for prompt, stream in [(case['ids'], False), (case['text'], False), (case['text'], True)]:
            requests.append(completion_request(case, prompt, model=model, stream=stream))
    requests.append(completion_request(cases[-1], cases[-1]['ids'], model=model, mode='length'))
    forced = dict(id='forced_eos', ids=cases[0]['ids'], generated=4)
    for stream in [False, True]:
        requests.append(completion_request(forced, forced['ids'], model=model, mode='forced_eos', stream=stream))
    requests.append(dict(model=model, prompt=cases[-1]['ids']+[cases[-1]['ids'][0]], max_tokens=1,
                         temperature=0, add_special_tokens=False))
    for payload in requests:
        if not payload.keys() <= protocol.CompletionRequest.model_fields.keys():
            raise ValueError('Requested field absent in actual protocol')
        request = protocol.CompletionRequest.model_validate(payload)
        if request.prompt != payload['prompt'] or request.add_special_tokens is not False:
            raise ValueError('Prompt or special-token settings changed')
        params = request.to_sampling_params(payload['max_tokens'], None)
        for key in ['max_tokens', 'temperature', 'ignore_eos', 'allowed_token_ids', 'min_tokens', 'skip_special_tokens']:
            if key in payload and getattr(params, key) != payload[key]:
                raise ValueError('Actual sampling mapping differs: '+key)
        if payload.get('stream'):
            if not request.stream_options.include_usage or request.stream_options.continuous_usage_stats:
                raise ValueError('Actual stream usage policy differs')
    response_checks = []
    for forced_eos in [False, True]:
        tokens = [1] if forced_eos else raw.encode('ABC').ids
        case = dict(id='protocol_response', ids=raw.encode('한글 123').ids, generated=4 if forced_eos else len(tokens))
        lp = protocol.CompletionLogProbs(tokens=[f'token_id:{t}' for t in tokens],
            token_logprobs=[-.5]*len(tokens), top_logprobs=[{f'token_id:{t}':-.5} for t in tokens],
            text_offset=list(range(len(tokens))))
        choice = protocol.CompletionResponseChoice(index=0, text=raw.decode(tokens, skip_special_tokens=True),
            token_ids=tokens, prompt_token_ids=case['ids'], finish_reason='stop' if forced_eos else 'length',
            logprobs=lp)
        usage = protocol.UsageInfo(prompt_tokens=len(case['ids']), completion_tokens=len(tokens),
                                   total_tokens=len(case['ids'])+len(tokens))
        response = protocol.CompletionResponse(id='cmpl-cpu-schema', model=model, created=1,
                                                choices=[choice], usage=usage)
        mode = 'forced_eos' if forced_eos else 'normal'
        checked = check_completion(dict(status=200, seconds=.1, body=json.loads(response.model_dump_json())),
                                   case, raw, model, mode=mode)
        stream_choice = protocol.CompletionResponseStreamChoice.model_validate({
            k:v for k,v in choice.model_dump().items()
            if k in protocol.CompletionResponseStreamChoice.model_fields})
        first = protocol.CompletionStreamResponse(id=response.id, created=1, model=model, choices=[stream_choice])
        last = protocol.CompletionStreamResponse(id=response.id, created=1, model=model, choices=[], usage=usage)
        wire = ('data: '+first.model_dump_json(exclude_unset=False)+'\n\ndata: '+
                last.model_dump_json(exclude_unset=False, exclude_none=True)+'\n\ndata: [DONE]\n\n').encode()
        parser = Events()
        for at in range(0, len(wire), 13):
            parser.feed(wire[at:at+13], .05)
        stream_reply = dict(status=200, seconds=.1, events=parser.finish())
        streamed = check_completion(assemble_stream(stream_reply, model), case, raw, model, mode=mode)
        if checked != streamed:
            raise ValueError('Actual response class roundtrip differs')
        response_checks.append(dict(forced_eos=forced_eos, token_ids=checked['token_ids']))
    if torch.cuda.is_initialized():
        raise ValueError('CPU probe initialized CUDA')
    source = Path(protocol.__file__)
    atomic_json(output, dict(status='passed_pinned_cpu_protocol_mapping_not_runtime',
        seconds=time.monotonic()-started, versions={n:importlib.metadata.version(n) for n in ['vllm', 'torch', 'transformers', 'tokenizers']},
        interpreter=sys.executable, protocol_source=str(source), protocol_sha256=sha256_file(source),
        code={n:sha256_file(resolve(n)) for n in ['scripts/check_serving_protocol.py', 'inference/serving_validation.py','inference/vllm_server.py']},
        server_argv=argv,server_options=expected,api_server_source_sha256=sha256_file(Path(api_server.__file__)),
        tokenizer_sha256=sha256_file(resolve('tokenizer/candidate_v1/tokenizer.json')),
        request_cases=len(requests), requests_sha256=hashlib.sha256(json.dumps(requests, sort_keys=True).encode()).hexdigest(),
        response_checks=response_checks, cuda_initialized=False, actual_server_executed=False,
        actual_final3b_execution_verified=False, synthetic_response_values=True, goal_complete=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True)
    main(p.parse_args().output)
