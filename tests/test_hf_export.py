"""REF-EXPORT/X01: actual installed Transformers, not an imitation reference."""
from dataclasses import replace
import json
from pathlib import Path

import pytest
import torch
from tokenizers import Tokenizer, models, pre_tokenizers, decoders, trainers, AddedToken
from transformers import AutoModelForCausalLM, AutoTokenizer

from inference.hf_export import (TOKENIZER_PROBES, layout, stage_package, to_hf,
                                 validate_state, write_tokenizer)
from model.model import ModelConfig, Transformer


def config(**kw):
    return ModelConfig(**dict(vocab_size=29, n_layers=2, dim=24, n_heads=6,
                             n_kv_heads=2, ffn_dim=40, max_seq_len=32,
                             rope_theta=12345., norm_eps=3e-4, **kw))


@pytest.mark.parametrize('tied', [False, True])
@pytest.mark.parametrize('kv', [1, 2, 6])
@pytest.mark.parametrize('attention', ['eager', 'sdpa'])
def test_actual_hf_logits_loss_cache_and_greedy(tied, kv, attention):
    torch.manual_seed(88)
    c = replace(config(tie_embeddings=tied), n_kv_heads=kv)
    native = Transformer(c).eval()
    hf = to_hf(native.state_dict(), c, attention=attention)
    assert hf.config.rms_norm_eps == c.norm_eps
    assert hf.config.rope_theta == c.rope_theta
    assert hf.config.tie_word_embeddings == tied
    assert (hf.lm_head.weight.data_ptr() == hf.model.embed_tokens.weight.data_ptr()) == tied
    assert hf.lm_head.weight.data_ptr() != native.out.weight.data_ptr()
    with torch.inference_mode():
        ids = torch.randint(3, c.vocab_size, (2, 17))
        for length in (1, 7, 17, 32):
            x = torch.randint(3, c.vocab_size, (2, length))
            torch.testing.assert_close(native(x), hf(x).logits, atol=1e-5, rtol=1e-4)
        # HF shifts labels internally; native accepts explicitly shifted targets.
        torch.testing.assert_close(native(ids[:, :-1].contiguous(), ids[:, 1:].contiguous()),
                                   hf(ids, labels=ids).loss, atol=1e-5, rtol=1e-4)
        prefix = hf(ids[:, :12], use_cache=True)
        tail = hf(ids[:, 12:], past_key_values=prefix.past_key_values, use_cache=True)
        torch.testing.assert_close(native(ids)[:, 12:], tail.logits, atol=1e-5, rtol=1e-4)
        # Actual GenerationMixin uses cached decoding; disable EOS stopping only
        # for this deterministic equal-length numerical comparison.
        expected = ids.clone()
        for _ in range(4):
            expected = torch.cat((expected, native(expected)[:, -1].argmax(-1, keepdim=True)), -1)
        actual = hf.generate(ids, attention_mask=torch.ones_like(ids), do_sample=False,
                             max_new_tokens=4, eos_token_id=None, pad_token_id=2)
        assert torch.equal(expected, actual)


def test_x01_missing_q_rejected_before_model_creation(monkeypatch):
    cases = json.loads(Path('docs/reviews/frankenstallm/acceptance_cases.json').read_text())
    x01 = next(x for x in cases['cases'] if x['id'] == 'X01')
    assert x01['expected']['reject_before_scoring']
    c = config(); state = Transformer(c).state_dict()
    del state['blocks.0.attn.wq.weight']
    def forbidden(*args, **kwargs):
        raise AssertionError('Missing weights must fail before HF model creation')
    monkeypatch.setattr('inference.hf_export.LlamaForCausalLM', forbidden)
    with pytest.raises(ValueError, match='missing=.*blocks.0.attn.wq.weight'):
        to_hf(state, c)


@pytest.mark.parametrize('mutation', ['extra', 'shape', 'integer', 'nan', 'tie'])
def test_bad_payload_rejected(mutation):
    c = config(tie_embeddings=mutation == 'tie')
    state = {k: v.clone() for k, v in Transformer(c).state_dict().items()}
    if mutation == 'extra': state['unexpected.weight'] = torch.zeros(1)
    if mutation == 'shape': state['blocks.0.attn.wk.weight'] = torch.zeros(c.dim, c.dim)
    if mutation == 'integer': state['norm.weight'] = state['norm.weight'].long()
    if mutation == 'nan': state['norm.weight'][0] = float('nan')
    if mutation == 'tie': state['out.weight'][0, 0] += 1
    with pytest.raises(ValueError):
        to_hf(state, c)


def test_actual_3b_geometry_and_untied_weight_mapping():
    c = ModelConfig()
    shapes = layout(c)
    assert len(shapes) == 255
    assert shapes['blocks.27.attn.wk.weight'][1] == (1024, 3072)
    small = Transformer(config()).state_dict()
    small['tok.weight'].fill_(.125)
    small['out.weight'].fill_(.375)
    hf = to_hf(small, config())
    assert torch.equal(hf.lm_head.weight, small['out.weight'])
    assert not torch.equal(hf.lm_head.weight, hf.model.embed_tokens.weight)
    validate_state(small, config())
    with pytest.raises(ValueError, match='overflow'):
        small['norm.weight'].fill_(1e30)
        to_hf(small, config(), dtype=torch.float16)


def fixture_tokenizer(path):
    tok = Tokenizer(models.BPE())
    tok.pre_tokenizer = pre_tokenizers.Sequence([
        pre_tokenizers.Digits(individual_digits=True), pre_tokenizers.ByteLevel(add_prefix_space=False)])
    tok.decoder = decoders.ByteLevel()
    special = [AddedToken(x, normalized=False, special=True) for x in
               ['<|bos|>', '<|eos|>', '<|pad|>', '<|im_start|>', '<|im_end|>']]
    tok.train_from_iterator(TOKENIZER_PROBES, trainers.BpeTrainer(
        vocab_size=300, initial_alphabet=pre_tokenizers.ByteLevel.alphabet(), special_tokens=special))
    tok.save(str(path))
    return tok


@pytest.mark.parametrize('tied', [False, True])
def test_actual_safetensor_shards_tokenizer_and_reload(tmp_path, tied):
    tokenizer = tmp_path/'source.json'; tok = fixture_tokenizer(tokenizer)
    c = replace(config(tie_embeddings=tied), vocab_size=tok.get_vocab_size())
    native = Transformer(c).eval(); original = {k: v.clone() for k, v in native.state_dict().items()}
    out = tmp_path/'staged'
    report = stage_package(original, c, tokenizer, out, max_shard_size='8KB')
    assert report['status'] == 'staged_not_released'
    assert not report['vllm_verified'] and not report['final_training_verified']
    assert (out/'model.safetensors.index.json').is_file()
    assert not (out/'.done').exists()
    hf = AutoModelForCausalLM.from_pretrained(out, local_files_only=True)
    fast = AutoTokenizer.from_pretrained(out, local_files_only=True)
    ids = torch.tensor([tok.encode(TOKENIZER_PROBES[1]).ids[:20]])
    with torch.inference_mode():
        torch.testing.assert_close(native(ids), hf(ids).logits, atol=1e-5, rtol=1e-4)
    assert (out/'tokenizer.json').read_bytes() == tokenizer.read_bytes()
    assert fast.chat_template is None
    assert all(torch.equal(v, native.state_dict()[k]) for k, v in original.items())
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    with pytest.raises(FileExistsError):
        stage_package(original, c, tokenizer, out)
    assert before == {p.name: p.read_bytes() for p in out.iterdir()}


def test_actual_final_tokenizer_roundtrip(tmp_path):
    source = Path('tokenizer/candidate_v1/tokenizer.json')
    report = write_tokenizer(source, tmp_path, ModelConfig())
    assert report['vocab_size'] == 65536 and report['probes'] == 24
    tok = AutoTokenizer.from_pretrained(tmp_path, local_files_only=True)
    # Digit-splitting and raw bytes must survive the generic HF wrapper.
    base = Tokenizer.from_file(str(source))
    for text in [*TOKENIZER_PROBES, '\n\n123000789\t끝', '\ufeff\u200b\ufffd']:
        assert tok.encode(text) == base.encode(text, add_special_tokens=False).ids
    before = (tmp_path/'tokenizer.json').read_bytes()
    with pytest.raises(ValueError, match='overwrite'):
        write_tokenizer(source, tmp_path, ModelConfig())
    assert before == (tmp_path/'tokenizer.json').read_bytes()


def test_failed_staging_preserves_partial_and_refuses_retry(tmp_path):
    source = tmp_path/'bad.json'; source.write_text('{}')
    out = tmp_path/'partial'; c = config(); state = Transformer(c).state_dict()
    with pytest.raises(Exception):
        stage_package(state, c, source, out)
    assert (out/'config.json').is_file()
    assert not (out/'conversion_report.json').exists()
    assert not (out/'.done').exists()
    with pytest.raises(FileExistsError):
        stage_package(state, c, source, out)
