import copy
from types import SimpleNamespace as NS

import pytest
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace

from inference.vllm_validation import build_cases, validate_outputs, check_engine_outputs, FIXED_NAMES


def tokenizer():
    t=Tokenizer(WordLevel({'[UNK]':0, 'hello':1, 'world':2}, unk_token='[UNK]'))
    t.pre_tokenizer=Whitespace()
    return t


def outputs(cases):
    return [NS(finished=True,prompt_token_ids=r['ids'][:],
        prompt_logprobs=[None]+[{t:NS(logprob=-1.)} for t in r['ids'][1:]],
        outputs=[NS(index=0,token_ids=[1]*r['generated'],finish_reason='length',stop_reason=None,
            logprobs=[{1:NS(logprob=-.5)} for _ in range(r['generated'])],
            cumulative_logprob=-.5*r['generated'])]) for r in cases]


def cases():
    return build_cases(tokenizer(),[dict(id=n,text='hello world') for n in sorted(FIXED_NAMES)],context=64,generated=4)


def test_boundary_and_scored_coverage():
    c=cases(); r=validate_outputs(c,outputs(c),tokenizer(),context=64)
    assert len(r)==12 and r[-1]['prompt_scored_targets']==62
    assert r[-2]['prompt_mean_nll'] is None
    assert r[0]['prompt_mean_nll']==1.


@pytest.mark.parametrize('failure', ['missing_request','prompt_changed','unfinished','short_output',
    'bad_token','nan','missing_logprob','sum_mismatch','prompt_coverage','early_stop'])
def test_reject_incomplete_or_corrupt_runtime_output(failure):
    c=cases(); o=outputs(c); x=o[0]; y=x.outputs[0]
    if failure=='missing_request':o.pop()
    if failure=='prompt_changed':x.prompt_token_ids=[2]
    if failure=='unfinished':x.finished=False
    if failure=='short_output':y.token_ids.pop()
    if failure=='bad_token':y.token_ids[0]=3
    if failure=='nan':y.logprobs[0][1].logprob=float('nan')
    if failure=='missing_logprob':y.logprobs[0]={}
    if failure=='sum_mismatch':y.cumulative_logprob=-99.
    if failure=='prompt_coverage':x.prompt_logprobs.pop()
    if failure=='early_stop':y.finish_reason='stop'
    with pytest.raises(ValueError):validate_outputs(c,o,tokenizer(),context=64)


def test_generate_calls_and_tokenizer_guard():
    c=cases(); raw=tokenizer(); guards=[]
    class Engine:
        def get_tokenizer(self):
            return NS(encode=lambda s,add_special_tokens:raw.encode(s,add_special_tokens=False).ids)
        def generate(self,prompts,**kwargs):
            assert all(p.ignore_eos and not p.detokenize and p.temperature==0 for p in kwargs['sampling_params'])
            return outputs(c if isinstance(prompts[0],dict) else c[:-2])
    result=check_engine_outputs(Engine(),raw,c,NS,guard=lambda:guards.append(1),context=64)
    assert len(guards)==3 and not result['actual_engine_execution_proven']
    altered=copy.deepcopy(c);altered[0]['ids']=[2]
    with pytest.raises(ValueError,match='tokenizer'):
        check_engine_outputs(Engine(),raw,altered,NS,guard=lambda:None,context=64)
