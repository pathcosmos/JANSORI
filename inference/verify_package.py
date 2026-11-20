"""Numerical and immutable publication primitives for a prepared HF package.

These functions do not certify final training or launch inference servers.
The production controller must authenticate the source and enforce shared
resource budgets through the mandatory live guard before publication.
"""
import json
import math
import os
from pathlib import Path

import torch
import torch.nn.functional as F
from tokenizers import Tokenizer
from transformers import AutoModelForCausalLM, AutoTokenizer

from inference.hf_export import sha256


def exact(path):
    path = Path(os.path.abspath(path))
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError('Package path symlink rejected')
    return path


def payload_manifest(directory, *, exclude=()):
    directory = exact(directory)
    result = {}
    for p in sorted(directory.rglob('*')):
        exact(p)
        if p.is_dir():
            raise ValueError('HF package must have a flat file inventory')
        if not p.is_file():
            raise ValueError('Nonregular package entry')
        if p.name in exclude:
            continue
        before = p.stat()
        digest = sha256(p)
        after = p.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('Package changed during SHA verification')
        result[p.name] = dict(bytes=after.st_size, sha256=digest)
    return result


def file_stamps(directory):
    """Reuse fresh content hashes only while inode/ctime/mtime/size remain exact."""
    directory = exact(directory); result = {}
    for p in sorted(directory.iterdir()):
        exact(p)
        if not p.is_file():
            raise ValueError('Unexpected non-file in package')
        s = p.stat()
        result[p.name] = [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]
    return result


def build_cases(tokenizer, prompts, *, context_length):
    tok = Tokenizer.from_file(str(tokenizer))
    rows = [json.loads(line) for line in Path(prompts).read_text().splitlines() if line.strip()]
    names = {'ko_explain', 'ko_math', 'ko_code', 'en_story', 'en_math', 'en_code'}
    if len(rows) != 6 or {r['id'] for r in rows} != names or context_length < 8:
        raise ValueError('Require the six fixed native prompts and valid context')
    result = []
    for row in rows:
        ids = tok.encode(row['text'], add_special_tokens=False).ids
        if not 2 <= len(ids) <= context_length-4:
            raise ValueError('Fixed prompt length outside conversion comparison budget')
        result.append(dict(id=row['id'], category=row['category'], ids=ids, greedy_tokens=4))
    seed = sum((r['ids'] for r in result), [])
    full = (seed * math.ceil(context_length/len(seed)))[:context_length]
    result.extend([
        dict(id='one_token', category='synthetic_boundary', ids=seed[:1], greedy_tokens=0),
        dict(id='full_context', category='synthetic_boundary', ids=full, greedy_tokens=0),
    ])
    return result


def compare_fp32(native, hf, cases, *, guard):
    """Compare full-vocabulary outputs at every supplied position on CPU.

    Fixed prompts test cache/greedy decoding; full-context synthetic input is a
    conversion boundary check, never a held-out PPL or model-quality benchmark.
    """
    if not callable(guard):
        raise ValueError('A live source/resource guard is required')
    c = native.c
    fields = dict(vocab_size=c.vocab_size, hidden_size=c.dim, intermediate_size=c.ffn_dim,
                  num_hidden_layers=c.n_layers, num_attention_heads=c.n_heads,
                  num_key_value_heads=c.n_kv_heads, max_position_embeddings=c.max_seq_len,
                  rms_norm_eps=c.norm_eps, rope_theta=c.rope_theta,
                  tie_word_embeddings=c.tie_embeddings, attention_bias=False, mlp_bias=False,
                  attention_dropout=0., hidden_act='silu', rope_scaling=None)
    if (hf.config.model_type != 'llama' or
            any(getattr(hf.config, k, None) != v for k, v in fields.items())):
        raise ValueError('HF/native configuration differs')
    if any(p.device.type != 'cpu' or p.dtype != torch.float32
           for model in (native, hf) for p in model.parameters()):
        raise ValueError('This numerical gate requires CPU FP32 models')
    if not cases or len({r['id'] for r in cases}) != len(cases):
        raise ValueError('Empty or duplicate numerical cases')
    native.eval(); hf.eval(); results = []

    def compare(a, b):
        if a.shape != b.shape:
            raise ValueError('Converted logits shape differs')
        error = 0.
        for start in range(0, a.shape[1], 128):
            x, y = a[:, start:start+128], b[:, start:start+128]
            if not bool(torch.isfinite(x).all() & torch.isfinite(y).all()):
                raise ValueError('Nonfinite conversion output')
            torch.testing.assert_close(x, y, atol=1e-5, rtol=1e-4)
            error = max(error, float((x-y).abs().max()))
        return error

    with torch.inference_mode():
        for row in cases:
            guard()
            ids = row['ids']; generated = row['greedy_tokens']
            if (not ids or any(type(x) is not int or not 0 <= x < c.vocab_size for x in ids) or
                    type(generated) is not int or not 0 <= generated <= 4 or len(ids)+generated > c.max_seq_len):
                raise ValueError('Unreviewed numerical input or generation length')
            x = torch.tensor([ids], dtype=torch.long)
            reference = native(x); converted = hf(x, use_cache=False).logits
            error = compare(reference, converted)
            result = dict(id=row['id'], category=row['category'], positions=len(ids),
                          full_vocabulary=c.vocab_size, max_absolute_error=error)
            if len(ids) > 1:
                targets = x[:, 1:].flatten()
                a = F.cross_entropy(reference[:, :-1].reshape(-1, c.vocab_size), targets)
                b = F.cross_entropy(converted[:, :-1].reshape(-1, c.vocab_size), targets)
                torch.testing.assert_close(a, b, atol=1e-5, rtol=1e-4)
                result.update(native_nll=float(a), hf_nll=float(b), scored_targets=len(ids)-1)
                split = max(1, len(ids)-min(8, len(ids)//2))
                prefix = hf(x[:, :split], use_cache=True)
                cached = hf(x[:, split:], past_key_values=prefix.past_key_values, use_cache=True)
                result['cache_max_absolute_error'] = compare(reference[:, split:], cached.logits)
                del prefix, cached
            del reference, converted
            if generated:
                expected = x
                for _ in range(generated):
                    expected = torch.cat([expected, native(expected)[:, -1].argmax(-1, keepdim=True)], dim=-1)
                actual = hf.generate(x, attention_mask=torch.ones_like(x), do_sample=False,
                                     max_new_tokens=generated, eos_token_id=None, pad_token_id=2)
                if not torch.equal(expected, actual):
                    raise ValueError('Cached greedy conversion tokens differ')
                result['greedy_ids'] = actual[0, len(ids):].tolist()
            guard(); results.append(result)
    return dict(status='passed_cpu_fp32_conversion', atol=1e-5, rtol=1e-4,
                cases=results, case_inputs=cases, device='cpu', dtype='float32',
                source_final_verified=False, vllm_verified=False, ollama_verified=False,
                scope='Numerical conversion equivalence only; no claim of practical generation quality.')


def verify_stage(directory, native, tokenizer, cases, *, guard):
    directory = exact(directory)
    if os.path.lexists(directory/'numerical_validation.json') or os.path.lexists(directory/'release_manifest.json'):
        raise ValueError('Preserve existing validation/publication attempt')
    guard()
    report = json.loads((directory/'conversion_report.json').read_text())
    before = file_stamps(directory)
    original = payload_manifest(directory)
    manifest = {k:v for k,v in original.items() if k != 'conversion_report.json'}
    if (report['status'] != 'staged_not_released' or report['dtype'] != 'float32' or
            manifest != report['files'] or report['tokenizer']['sha256'] != sha256(tokenizer) or
            sha256(directory/'tokenizer.json') != sha256(tokenizer)):
        raise ValueError('Staged package content/source tokenizer changed')
    if file_stamps(directory) != before:
        raise ValueError('Package changed across initial SHA verification')
    hf, info = AutoModelForCausalLM.from_pretrained(directory, local_files_only=True,
        trust_remote_code=False, torch_dtype=torch.float32, attn_implementation='eager', output_loading_info=True)
    if any(info.get(k) for k in ('missing_keys', 'unexpected_keys', 'mismatched_keys', 'error_msgs')):
        raise ValueError('Reloaded conversion has missing/unexpected weights')
    fast = AutoTokenizer.from_pretrained(directory, local_files_only=True, trust_remote_code=False)
    base = Tokenizer.from_file(str(tokenizer))
    if fast.chat_template is not None or len(fast) != native.c.vocab_size:
        raise ValueError('Conversion tokenizer/chat policy differs')
    for row in cases:
        text = base.decode(row['ids'], skip_special_tokens=False)
        if (fast.encode(text) != base.encode(text, add_special_tokens=False).ids or
                fast.decode(row['ids'], skip_special_tokens=False) != text):
            raise ValueError('Conversion tokenizer IDs/decode differ')
    result = compare_fp32(native, hf, cases, guard=guard)
    if file_stamps(directory) != before:
        raise ValueError('Staged package changed during numerical verification')
    result.update(package_files=original, source_tokenizer_sha256=sha256(tokenizer))
    guard()
    with (directory/'numerical_validation.json').open('x') as f:
        json.dump(result, f, indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
    return result


def fsync_dir(path):
    fd = os.open(path, os.O_DIRECTORY | os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish_stage(stage, destination, *, source_binding, validation, guard, fault=None):
    """Publish one verified directory, with caller-supplied live source admission.

    NFS lacks RENAME_NOREPLACE here. Reserve destination with exclusive mkdir,
    hard-link immutable files on the same filesystem, verify their SHA, then
    atomically create the release manifest via a no-clobber hard link. Readers
    MUST require that manifest. Both names are retained; allocated data blocks
    are shared, but budget accounting conservatively charges both logical sizes.
    Failures preserve source/partial output. No retry or cleanup is automatic.
    """
    stage, destination = exact(stage), exact(destination)
    if stage == destination or stage.parent != destination.parent:
        raise ValueError('Use distinct sibling staging/final package paths')
    if os.path.lexists(destination):
        raise FileExistsError('Preserve existing published package')
    if not source_binding or not callable(guard):
        raise ValueError('Explicit source binding and live admission guard required')
    if validation.get('status') != 'passed_cpu_fp32_conversion':
        raise ValueError('Actual numerical comparison missing')
    if json.loads((stage/'numerical_validation.json').read_text()) != validation:
        raise ValueError('Numerical receipt differs from this execution')
    before = file_stamps(stage)
    files = payload_manifest(stage)
    if {k:v for k,v in files.items() if k != 'numerical_validation.json'} != validation['package_files']:
        raise ValueError('Package changed after numerical comparison')
    if file_stamps(stage) != before:
        raise ValueError('Package changed across publication SHA verification')
    guard(); fault = fault or (lambda _: None)
    release = dict(status='verified_hf_fp32_runtime_pending', source_binding=source_binding,
                   numerical_validation_sha256=sha256(stage/'numerical_validation.json'),
                   files=files, vllm_verified=False, ollama_verified=False,
                   goal_complete=False, resumable_training_checkpoint=False,
                   publication_method='exclusive_directory_then_atomic_manifest_hardlink',
                   staging_preserved=True)
    with (stage/'release_manifest.json').open('x') as f:
        json.dump(release, f, indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
    before['release_manifest.json'] = file_stamps(stage)['release_manifest.json']
    for path in stage.iterdir():
        with path.open('rb') as f:
            os.fsync(f.fileno())
    fsync_dir(stage); fault('before_linking'); guard()
    # Reuse this call's fresh hashes only while all bytes' file identities and
    # exact directory inventory remain unchanged, including the new manifest.
    if file_stamps(stage) != before:
        raise ValueError('Package changed at publication boundary')
    if stage.stat().st_dev != destination.parent.stat().st_dev:
        raise ValueError('Publication requires the same filesystem')
    destination.mkdir(exist_ok=False)
    fsync_dir(destination.parent)
    for i, name in enumerate(files):
        os.link(stage/name, destination/name, follow_symlinks=False)
        if i == 0:
            fault('after_first_link')
    fsync_dir(destination)
    linked_before = file_stamps(destination)
    if payload_manifest(destination) != files or file_stamps(destination) != linked_before:
        raise ValueError('Linked package SHA/identity changed')
    manifest_bytes = (stage/'release_manifest.json').read_bytes()
    if json.loads(manifest_bytes) != release:
        raise ValueError('Prepared publication manifest changed')
    manifest_stamp = file_stamps(stage)['release_manifest.json']
    fault('before_commit'); guard()
    if (file_stamps(destination) != linked_before or
            file_stamps(stage)['release_manifest.json'] != manifest_stamp):
        raise ValueError('Package changed at manifest publication boundary')
    os.link(stage/'release_manifest.json', destination/'release_manifest.json', follow_symlinks=False)
    linked_before['release_manifest.json'] = file_stamps(destination)['release_manifest.json']
    fsync_dir(destination); fault('after_commit'); guard()
    if file_stamps(destination) != linked_before:
        raise ValueError('Published package changed immediately after commit')
    return release


def verify_published(directory, *, source_binding, guard):
    directory = exact(directory); guard()
    before = file_stamps(directory)
    raw = (directory/'release_manifest.json').read_bytes(); report = json.loads(raw)
    if (report['status'] != 'verified_hf_fp32_runtime_pending' or report['source_binding'] != source_binding or
            report['goal_complete'] is not False or report['vllm_verified'] is not False or report['ollama_verified'] is not False or
            report['resumable_training_checkpoint'] is not False or
            payload_manifest(directory, exclude=('release_manifest.json',)) != report['files'] or
            sha256(directory/'numerical_validation.json') != report['numerical_validation_sha256']):
        raise ValueError('Published package content/binding differs')
    validation = json.loads((directory/'numerical_validation.json').read_text())
    if (validation['status'] != 'passed_cpu_fp32_conversion' or
            {k:v for k,v in report['files'].items() if k != 'numerical_validation.json'} != validation['package_files']):
        raise ValueError('Published numerical receipt no longer binds payload')
    if (directory/'release_manifest.json').read_bytes() != raw:
        raise ValueError('Publication manifest changed while verifying')
    guard()
    if file_stamps(directory) != before:
        raise ValueError('Published payload changed while verifying')
    return report
