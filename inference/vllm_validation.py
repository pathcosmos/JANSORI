"""Bounded output checks for an already admitted vLLM engine.

No engine construction, GPU ownership, package authentication or publication
happens here. A production caller must supply those gates and a live guard.
Passing output checks alone never certifies actual final-model execution.
"""
import math


FIXED_NAMES = {'ko_explain', 'ko_math', 'ko_code', 'en_story', 'en_math', 'en_code'}
EDGE_TEXTS = [' 12345\r\n67890', '한글 e\u0301 café 😀',
              'def f(x):\n\treturn x + 12\n', '<|bos|> literal <|eos|>']


def build_cases(raw_tokenizer, prompts, *, context=4096, generated=32):
    if len(prompts) != 6 or {p['id'] for p in prompts} != FIXED_NAMES:
        raise ValueError('Require all six fixed base-model prompts')
    if type(context) is not int or context < 8 or type(generated) is not int or not 1 <= generated < context:
        raise ValueError('Invalid bounded context/generation')
    rows = [dict(id=p['id'], text=p['text']) for p in prompts]
    rows += [dict(id=f'tokenizer_edge_{i}', text=t) for i, t in enumerate(EDGE_TEXTS)]
    for row in rows:
        ids = raw_tokenizer.encode(row['text'], add_special_tokens=False).ids
        if not ids or len(ids) + generated > context:
            raise ValueError('Prompt outside runtime context budget')
        row.update(ids=ids, generated=generated)
    seed = rows[0]['ids']
    full = (seed * math.ceil((context-1)/len(seed)))[:context-1]
    rows += [dict(id='one_token', ids=seed[:1], generated=1),
             dict(id='context_boundary', ids=full, generated=1)]
    return rows


def checked_ids(values, vocab):
    values = list(values)
    if not values or any(type(t) is not int or not 0 <= t < vocab for t in values):
        raise ValueError('Empty or invalid token IDs')
    return values


def selected_logprobs(rows, ids):
    if rows is None or len(rows) != len(ids):
        raise ValueError('Missing log probability positions')
    result = []
    for row, token in zip(rows, ids):
        if not isinstance(row, dict) or token not in row:
            raise ValueError('Selected token log probability missing')
        for item in row.values():
            value = float(item.logprob)
            if not math.isfinite(value) or value > 1e-6:
                raise ValueError('Nonfinite or positive log probability')
        result.append(float(row[token].logprob))
    return result


def validate_outputs(cases, outputs, raw_tokenizer, *, context):
    if not cases or len(outputs) != len(cases) or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Missing or duplicate runtime cases')
    vocab = raw_tokenizer.get_vocab_size(with_added_tokens=True)
    results = []
    for case, output in zip(cases, outputs):
        ids = checked_ids(case['ids'], vocab)
        count = case['generated']
        if type(count) is not int or count < 1 or len(ids)+count > context:
            raise ValueError('Unreviewed generation length')
        if (not output.finished or output.prompt_token_ids != ids or len(output.outputs) != 1):
            raise ValueError('Unfinished, truncated or mismatched request')
        completion = output.outputs[0]
        tokens = checked_ids(completion.token_ids, vocab)
        if (completion.index != 0 or len(tokens) != count or completion.finish_reason != 'length'
                or completion.stop_reason is not None):
            raise ValueError('Unexpected completion/early stop')
        logps = selected_logprobs(completion.logprobs, tokens)
        total = float(completion.cumulative_logprob)
        if not math.isfinite(total) or not math.isclose(total, sum(logps), abs_tol=1e-4, rel_tol=1e-5):
            raise ValueError('Generated log probability sum differs')
        prompt = output.prompt_logprobs
        if prompt is None or len(prompt) != len(ids) or prompt[0] is not None:
            raise ValueError('Prompt log probability coverage differs')
        plps = selected_logprobs(prompt[1:], ids[1:])
        results.append(dict(id=case['id'], prompt_tokens=len(ids), generated_ids=tokens,
            generated_text=raw_tokenizer.decode(tokens, skip_special_tokens=False),
            generated_logprob_sum=total, prompt_scored_targets=len(plps),
            prompt_nll_sum=-sum(plps), prompt_mean_nll=-sum(plps)/len(plps) if plps else None,
            finish_reason=completion.finish_reason))
    return results


def check_engine_outputs(engine, raw_tokenizer, cases, sampling_params, *, guard, context=4096):
    """Two real generate calls when supplied a real engine; mocks are not proof.

    Ignore EOS only in this fixed-length diagnostic, never as a user-facing
    generation default. Prompt NLL here is a smoke metric, not held-out PPL.
    """
    if not callable(guard):
        raise ValueError('Live source/resource guard required')
    guard()
    tokenizer = engine.get_tokenizer()
    for case in cases:
        if 'text' in case:
            if tokenizer.encode(case['text'], add_special_tokens=False) != case['ids']:
                raise ValueError('Engine tokenizer differs from original')

    def params(rows):
        return [sampling_params(n=1, temperature=0, seed=0, max_tokens=r['generated'],
            min_tokens=r['generated'], ignore_eos=True, logprobs=1, prompt_logprobs=1,
            detokenize=False) for r in rows]

    token_outputs = engine.generate([{'prompt_token_ids':r['ids']} for r in cases],
                                   sampling_params=params(cases), use_tqdm=False)
    guard()
    token_rows = validate_outputs(cases, token_outputs, raw_tokenizer, context=context)
    text_cases = [r for r in cases if 'text' in r]
    text_outputs = engine.generate([r['text'] for r in text_cases],
                                  sampling_params=params(text_cases), use_tqdm=False)
    guard()
    text_rows = validate_outputs(text_cases, text_outputs, raw_tokenizer, context=context)
    by_id = {r['id']:r for r in token_rows}
    if any(r['generated_ids'] != by_id[r['id']]['generated_ids'] for r in text_rows):
        raise ValueError('Text/token input greedy outputs differ')
    return dict(status='passed_output_checks_execution_provenance_required',
        token_cases=token_rows, text_cases=text_rows, fixed_diagnostic_ignore_eos=True,
        package_authentication_verified=False, actual_engine_execution_proven=False,
        native_fp32_equivalence_claimed=False, quality_benchmark=False, goal_complete=False)
