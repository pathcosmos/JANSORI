"""Strict native -> HF Llama conversion primitives (no production launcher).

Callers must certify the checkpoint, input/code lineage, final step, resource
budget and numerical/runtime results separately. A saved staging directory is
NOT a released package, a resumable checkpoint, or a vLLM/Ollama acceptance.
"""
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path

import torch
from tokenizers import Tokenizer
from transformers import (AutoModelForCausalLM, AutoTokenizer, LlamaConfig,
                          LlamaForCausalLM, PreTrainedTokenizerFast)

from model.model import ModelConfig


TOKENIZER_PROBES = (
    '', '한글 언어 모델입니다.\n새 줄과  두 칸\t탭',
    'The quick brown fox.\r\n Space before and after ',
    '1234567890 + 001.25 = ?\n\\frac{a}{b}',
    'def f(x):\n    return x ** 2  # 주석\n',
    '😀 café e\u0301 中文 العربية\u0000', '<|bos|>text<|eos|>',
    '<|im_start|>user\nquestion<|im_end|>',
)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''):
            h.update(block)
    return h.hexdigest()


def layout(c):
    """Return exhaustive source -> (HF name, expected shape), without a model."""
    if not isinstance(c, ModelConfig):
        raise ValueError('Require the actual native ModelConfig')
    for field in ('vocab_size', 'n_layers', 'dim', 'n_heads', 'n_kv_heads',
                  'ffn_dim', 'max_seq_len'):
        value = getattr(c, field)
        if type(value) is not int or value < 1:
            raise ValueError(f'Invalid model dimension: {field}')
    if (c.dim % c.n_heads or c.n_heads % c.n_kv_heads or
            (c.dim // c.n_heads) % 2 or type(c.tie_embeddings) is not bool):
        raise ValueError('Unsupported attention/tie geometry')
    if any(not math.isfinite(x) or x <= 0 for x in (c.rope_theta, c.norm_eps)):
        raise ValueError('Invalid RoPE or RMSNorm constant')
    d, f, kv = c.dim, c.ffn_dim, c.n_kv_heads * (c.dim // c.n_heads)
    result = {
        'tok.weight': ('model.embed_tokens.weight', (c.vocab_size, d)),
        'norm.weight': ('model.norm.weight', (d,)),
        'out.weight': ('lm_head.weight', (c.vocab_size, d)),
    }
    per_layer = {
        'attn_norm.weight': ('input_layernorm.weight', (d,)),
        'ffn_norm.weight': ('post_attention_layernorm.weight', (d,)),
        'attn.wq.weight': ('self_attn.q_proj.weight', (d, d)),
        'attn.wk.weight': ('self_attn.k_proj.weight', (kv, d)),
        'attn.wv.weight': ('self_attn.v_proj.weight', (kv, d)),
        'attn.wo.weight': ('self_attn.o_proj.weight', (d, d)),
        'ffn.w1.weight': ('mlp.gate_proj.weight', (f, d)),
        'ffn.w3.weight': ('mlp.up_proj.weight', (f, d)),
        'ffn.w2.weight': ('mlp.down_proj.weight', (d, f)),
    }
    for i in range(c.n_layers):
        result.update({f'blocks.{i}.{k}': (f'model.layers.{i}.{v}', shape)
                       for k, (v, shape) in per_layer.items()})
    return result


def validate_state(state, c):
    expected = layout(c)
    if set(state) != set(expected):
        raise ValueError(f'Native weights differ: missing={sorted(set(expected)-set(state))}; '
                         f'unexpected={sorted(set(state)-set(expected))}')
    for key, (_, shape) in expected.items():
        t = state[key]
        if (not isinstance(t, torch.Tensor) or t.device.type != 'cpu' or
                t.layout != torch.strided or tuple(t.shape) != shape or
                t.dtype not in (torch.float32, torch.bfloat16, torch.float16)):
            raise ValueError(f'Invalid CPU weight/shape/dtype: {key}')
        if not bool(torch.isfinite(t).all()):
            raise ValueError(f'Nonfinite native weight: {key}')
    if c.tie_embeddings and not torch.equal(state['tok.weight'], state['out.weight']):
        raise ValueError('Tied configuration has distinct embedding/head weights')
    return expected


def to_hf(state, c, *, dtype=torch.float32, attention='eager'):
    """No random fill, q/k permutation, embedding/head substitution or GPU use."""
    expected = validate_state(state, c)
    if dtype not in (torch.float32, torch.bfloat16, torch.float16):
        raise ValueError('Unsupported export dtype')
    if attention not in ('eager', 'sdpa'):
        raise ValueError('Unreviewed attention implementation')
    config = LlamaConfig(
        vocab_size=c.vocab_size, hidden_size=c.dim, intermediate_size=c.ffn_dim,
        num_hidden_layers=c.n_layers, num_attention_heads=c.n_heads,
        num_key_value_heads=c.n_kv_heads, max_position_embeddings=c.max_seq_len,
        rope_theta=c.rope_theta, rms_norm_eps=c.norm_eps,
        tie_word_embeddings=c.tie_embeddings, hidden_act='silu',
        attention_bias=False, mlp_bias=False, attention_dropout=0.,
        bos_token_id=0, eos_token_id=1, pad_token_id=2,
        torch_dtype=dtype, use_cache=True,
    )
    config._attn_implementation = attention
    with torch.device('meta'):
        model = LlamaForCausalLM(config)
    weights = {}
    for key, (dest, _) in expected.items():
        # Own output storage; HF tying/casting must never change the source.
        weights[dest] = state[key].detach().to(dtype=dtype, copy=True).contiguous()
        if not bool(torch.isfinite(weights[dest]).all()):
            raise ValueError(f'Export dtype overflow: {key}')
    model.load_state_dict(weights, strict=True, assign=True)
    model.tie_weights()
    # RoPE's non-persistent inverse-frequency buffer was constructed on meta.
    from transformers.models.llama.modeling_llama import LlamaRotaryEmbedding
    model.model.rotary_emb = LlamaRotaryEmbedding(config, device='cpu')
    if any(t.device.type != 'cpu' for t in [*model.parameters(), *model.buffers()]):
        raise ValueError('Unmaterialized export tensor')
    return model.eval()


def write_tokenizer(source, directory, c):
    """Write metadata in an owned staging directory; preserve raw JSON bytes."""
    source, directory = Path(source), Path(directory)
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError('Require an existing owned staging directory')
    names = ('tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json')
    if any(os.path.lexists(directory / name) for name in names):
        raise ValueError('Refuse tokenizer overwrite')
    raw = source.read_bytes()
    spec = json.loads(raw)
    tok = Tokenizer.from_str(raw.decode('utf-8'))
    if (tok.get_vocab_size(with_added_tokens=True) != c.vocab_size or
            set(tok.get_vocab().values()) != set(range(c.vocab_size))):
        raise ValueError('Tokenizer/model vocabulary differs')
    required = ('<|bos|>', '<|eos|>', '<|pad|>')
    if [tok.token_to_id(x) for x in required] != [0, 1, 2]:
        raise ValueError('Unexpected tokenizer BOS/EOS/PAD IDs')
    # This base model was trained without insertion, normalization or padding.
    if any(spec.get(k) is not None for k in ('normalizer', 'post_processor', 'padding', 'truncation')):
        raise ValueError('Unreviewed tokenizer preprocessing/insertion policy')
    added = spec.get('added_tokens', [])
    if any(not a.get('special') or a.get('normalized') for a in added):
        raise ValueError('Unreviewed added-token policy')
    specials = [a['content'] for a in added if a['content'] not in required]
    fast = PreTrainedTokenizerFast(
        tokenizer_object=tok, bos_token=required[0], eos_token=required[1],
        pad_token=required[2], additional_special_tokens=specials,
        model_max_length=c.max_seq_len, clean_up_tokenization_spaces=False,
    )
    fast.save_pretrained(directory)
    # HF serialization may change formatting. The trained JSON is authoritative.
    (directory / 'tokenizer.json').write_bytes(raw)
    loaded = AutoTokenizer.from_pretrained(directory, local_files_only=True,
                                          trust_remote_code=False)
    if loaded.chat_template is not None or len(loaded) != c.vocab_size:
        raise ValueError('Unexpected chat template or vocabulary change')
    probes = list(TOKENIZER_PROBES) + [a['content'] for a in added]
    for text in probes:
        ids = tok.encode(text, add_special_tokens=False).ids
        for auto in (False, True):
            if loaded.encode(text, add_special_tokens=auto) != ids:
                raise ValueError('Token IDs or automatic special insertion changed')
        for skip in (False, True):
            if loaded.decode(ids, skip_special_tokens=skip) != tok.decode(ids, skip_special_tokens=skip):
                raise ValueError('Tokenizer decoder changed')
    if source.read_bytes() != raw or (directory / 'tokenizer.json').read_bytes() != raw:
        raise ValueError('Tokenizer source changed during packaging')
    return dict(sha256=hashlib.sha256(raw).hexdigest(), vocab_size=len(loaded),
                bos=0, eos=1, pad=2, probes=len(probes), chat_template=None,
                automatic_bos_eos=False)


def stage_package(state, c, tokenizer, directory, *, max_shard_size='2GB'):
    """FP32 staging only. Existing/partial outputs are preserved and refused.

    A separate final controller must supply certified source evidence and do
    numerical/runtime validation before publication. No .done is written here.
    """
    directory = Path(directory)
    model = to_hf(state, c)
    directory.mkdir(exist_ok=False)
    model.save_pretrained(directory, safe_serialization=True, max_shard_size=max_shard_size)
    token_report = write_tokenizer(tokenizer, directory, c)
    loaded, info = AutoModelForCausalLM.from_pretrained(
        directory, local_files_only=True, trust_remote_code=False,
        torch_dtype=torch.float32, attn_implementation='eager', output_loading_info=True)
    if any(info.get(k) for k in ('missing_keys', 'unexpected_keys', 'mismatched_keys', 'error_msgs')):
        raise ValueError(f'Packaged HF loading mismatch: {info}')
    for key, t in model.state_dict().items():
        if not torch.equal(t, loaded.state_dict()[key]):
            raise ValueError(f'Packaged HF weight differs: {key}')
    del loaded
    report = dict(status='staged_not_released', native_config=asdict(c), dtype='float32',
                  tokenizer=token_report, inference_only=True, checkpoint_resume=False,
                  final_training_verified=False, numerical_equivalence_verified=False,
                  vllm_verified=False, ollama_verified=False,
                  files={p.name: dict(bytes=p.stat().st_size, sha256=sha256(p))
                         for p in sorted(directory.iterdir()) if p.is_file()})
    (directory / 'conversion_report.json').write_text(json.dumps(report, indent=2)+'\n')
    return report
