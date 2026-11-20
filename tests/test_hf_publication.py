"""Real tiny HF conversion plus actual local/NFS publication fault checks."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile

import pytest
import torch
from tokenizers import Tokenizer, models, pre_tokenizers, decoders, trainers, AddedToken

from inference.hf_export import stage_package, to_hf, TOKENIZER_PROBES
from inference.verify_package import (build_cases, compare_fp32, verify_stage,
    publish_stage, verify_published, file_stamps)
from model.model import ModelConfig, Transformer


def fixture(root):
    tok = Tokenizer(models.BPE())
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tok.decoder = decoders.ByteLevel()
    special = [AddedToken(x, special=True, normalized=False) for x in ['<|bos|>', '<|eos|>', '<|pad|>']]
    tok.train_from_iterator(TOKENIZER_PROBES, trainers.BpeTrainer(vocab_size=280,
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(), special_tokens=special))
    path = root/'tokenizer.json'; tok.save(str(path))
    c = ModelConfig(vocab_size=tok.get_vocab_size(), n_layers=1, dim=16, n_heads=4,
                    n_kv_heads=2, ffn_dim=24, max_seq_len=16)
    torch.manual_seed(73); native = Transformer(c).eval()
    cases = [dict(id='short', category='ko', ids=[5, 9, 7], greedy_tokens=4),
             dict(id='full_context', category='synthetic_boundary', ids=list(range(3, 19)), greedy_tokens=0)]
    stage = root/'hf.copying'
    stage_package(native.state_dict(), c, path, stage, max_shard_size='8KB')
    return native, path, stage, cases


def no_op():
    pass


@pytest.mark.parametrize('backend', ['local', 'nfs'])
def test_real_hf_validation_publication_and_independent_reopen(tmp_path, backend):
    # NFS operation is exercised on the actual project mount, not mocked.
    base = Path('runs/main_training_35k_plan_v1').resolve() if backend == 'nfs' else tmp_path
    with tempfile.TemporaryDirectory(prefix='hf-publication-fixture-', dir=base) as name:
        root = Path(name); native, tok, stage, cases = fixture(root)
        before = {k:v.clone() for k,v in native.state_dict().items()}
        validation = verify_stage(stage, native, tok, cases, guard=no_op)
        assert validation['cases'][1]['positions'] == 16
        assert validation['source_final_verified'] is False
        binding = dict(scope='tiny_cpu_fixture_not_final_training', tokenizer=str(tok))
        published = root/'hf'
        release = publish_stage(stage, published, source_binding=binding, validation=validation, guard=no_op)
        assert stage.exists() and published.is_dir()
        assert (stage/'config.json').stat().st_ino == (published/'config.json').stat().st_ino
        assert verify_published(published, source_binding=binding, guard=no_op) == release
        assert release['vllm_verified'] is False and release['goal_complete'] is False
        assert not (published/'.done').exists()
        assert all(torch.equal(v, native.state_dict()[k]) for k,v in before.items())


def test_actual_fixed_six_prompts_full_context_and_final_tokenizer():
    cases = build_cases('tokenizer/candidate_v1/tokenizer.json','runs/f7_preflight_v1/prompts.jsonl',context_length=4096)
    assert len(cases) == 8 and len(cases[-1]['ids']) == 4096
    assert {r['id'] for r in cases[:6]} == {'ko_explain','ko_math','ko_code','en_story','en_math','en_code'}
    assert all(0 <= token < 65536 for row in cases for token in row['ids'])
    assert cases[-1]['greedy_tokens'] == 0


@pytest.mark.parametrize('point', ['before_linking','after_first_link','before_commit','after_commit'])
def test_interruption_preserves_staging_or_whole_published_package(tmp_path, point):
    native, tok, stage, cases = fixture(tmp_path)
    validation = verify_stage(stage, native, tok, cases, guard=no_op)
    binding = {'scope':'fixture'}; final = tmp_path/'hf'
    def fault(at):
        if at == point: raise InterruptedError('Simulated process interruption at publication boundary')
    with pytest.raises(InterruptedError):
        publish_stage(stage, final, source_binding=binding, validation=validation, guard=no_op, fault=fault)
    if point == 'before_linking':
        assert stage.exists() and not final.exists()
        assert (stage/'release_manifest.json').exists()
        with pytest.raises(ValueError):
            publish_stage(stage, final, source_binding=binding, validation=validation, guard=no_op)
    elif point == 'after_commit':
        assert final.exists() and stage.exists()
        assert verify_published(final, source_binding=binding, guard=no_op)['status'] == 'verified_hf_fp32_runtime_pending'
    else:
        assert stage.exists() and final.exists()
        assert not (final/'release_manifest.json').exists()
        with pytest.raises(FileNotFoundError):
            verify_published(final,source_binding=binding,guard=no_op)
        with pytest.raises(FileExistsError):
            publish_stage(stage,final,source_binding=binding,validation=validation,guard=no_op)


@pytest.mark.parametrize('fault_kind', ['mutate_payload','new_file','replace_inode'])
def test_boundary_change_refused(tmp_path, fault_kind):
    native, tok, stage, cases = fixture(tmp_path)
    validation = verify_stage(stage, native, tok, cases, guard=no_op)
    def fault(at):
        if at != 'before_linking': return
        target = stage/'config.json'
        if fault_kind == 'new_file': (stage/'unexpected').write_text('extra')
        elif fault_kind == 'replace_inode':
            raw = target.read_bytes(); target.unlink(); target.write_bytes(raw)
        else: target.write_bytes(target.read_bytes()+b' ')
    with pytest.raises(ValueError, match='publication boundary'):
        publish_stage(stage,tmp_path/'hf',source_binding={'scope':'fixture'},validation=validation,guard=no_op,fault=fault)
    assert stage.exists() and not (tmp_path/'hf').exists()


def test_budget_or_source_guard_failure_prevents_publication(tmp_path):
    native, tok, stage, cases = fixture(tmp_path)
    validation = verify_stage(stage, native, tok, cases, guard=no_op)
    before = file_stamps(stage)
    def fail(): raise RuntimeError('source changed or capacity unavailable')
    with pytest.raises(RuntimeError, match='capacity'):
        publish_stage(stage,tmp_path/'hf',source_binding={'scope':'fixture'},validation=validation,guard=fail)
    assert file_stamps(stage) == before and not (tmp_path/'hf').exists()


def test_existing_destination_and_cross_filesystem_are_never_replaced(tmp_path):
    native, tok, source, cases = fixture(tmp_path)
    validation = verify_stage(source,native,tok,cases,guard=no_op)
    before = file_stamps(source)
    dest = tmp_path/'dest'; dest.mkdir()
    with pytest.raises(FileExistsError):
        publish_stage(source,dest,source_binding={'scope':'fixture'},validation=validation,guard=no_op)
    assert before == file_stamps(source) and dest.is_dir()
    with tempfile.TemporaryDirectory(prefix='hf-cross-fs-fixture-',dir=Path('runs/main_training_35k_plan_v1').resolve()) as folder:
        assert Path(folder).stat().st_dev != tmp_path.stat().st_dev
        with pytest.raises(ValueError, match='sibling'):
            publish_stage(source,Path(folder)/'hf',source_binding={'scope':'fixture'},validation=validation,guard=no_op)
    assert source.exists()


def test_published_payload_corruption_and_wrong_source_rejected(tmp_path):
    native,tok,stage,cases=fixture(tmp_path)
    validation=verify_stage(stage,native,tok,cases,guard=no_op)
    binding={'scope':'fixture'}; dest=tmp_path/'hf'
    publish_stage(stage,dest,source_binding=binding,validation=validation,guard=no_op)
    with pytest.raises(ValueError,match='binding'):
        verify_published(dest,source_binding={'scope':'wrong'},guard=no_op)
    target=next(dest.glob('*.safetensors')); raw=target.read_bytes()
    target.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
    with pytest.raises(ValueError,match='content'):
        verify_published(dest,source_binding=binding,guard=no_op)


def test_real_weight_or_rope_error_fails_numerical_gate(tmp_path):
    native,_,_,cases=fixture(tmp_path)
    hf=to_hf(native.state_dict(),native.c)
    with torch.no_grad():hf.model.layers[0].self_attn.q_proj.weight.mul_(100)
    with pytest.raises(AssertionError):compare_fp32(native,hf,cases,guard=no_op)
    hf=to_hf(native.state_dict(),native.c);hf.config.rope_theta=100.
    with pytest.raises(ValueError,match='configuration'):compare_fp32(native,hf,cases,guard=no_op)


def test_stage_sha_change_rejected_before_loading(tmp_path,monkeypatch):
    native,tok,stage,cases=fixture(tmp_path)
    target=next(stage.glob('*.safetensors'));target.write_bytes(target.read_bytes()+b'broken')
    def forbidden(*a,**kw):raise AssertionError('must reject before model loading')
    monkeypatch.setattr('inference.verify_package.AutoModelForCausalLM.from_pretrained',forbidden)
    with pytest.raises(ValueError,match='content'):
        verify_stage(stage,native,tok,cases,guard=no_op)


def test_symlinks_and_unexpected_directories_rejected(tmp_path):
    source=tmp_path/'s';source.mkdir();(source/'x').symlink_to('/etc/hostname')
    with pytest.raises(ValueError,match='symlink'):file_stamps(source)
    (source/'x').unlink();(source/'sub').mkdir()
    with pytest.raises(ValueError,match='non-file'):file_stamps(source)
