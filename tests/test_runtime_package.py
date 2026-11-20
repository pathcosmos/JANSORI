import hashlib
import json
from pathlib import Path

import pytest

from inference.runtime_package import authenticate_release


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def fixture(root):
    root.mkdir()
    for name in ['config.json','tokenizer_config.json','tokenizer.json','conversion_report.json']:
        (root/name).write_text('{}')
    (root/'model.safetensors').write_bytes(b'synthetic_fixture_not_weights')
    files = {p.name:dict(bytes=p.stat().st_size,sha256=sha(p)) for p in root.iterdir()}
    numeric = dict(status='passed_cpu_fp32_conversion',source_tokenizer_sha256=sha(root/'tokenizer.json'),package_files=files.copy())
    (root/'numerical_validation.json').write_text(json.dumps(numeric))
    p = root/'numerical_validation.json';files[p.name]=dict(bytes=p.stat().st_size,sha256=sha(p))
    release = dict(status='verified_hf_fp32_runtime_pending',source_binding={'scope':'fixture_only'},
        goal_complete=False,vllm_verified=False,ollama_verified=False,resumable_training_checkpoint=False,
        files=files,numerical_validation_sha256=sha(p))
    (root/'release_manifest.json').write_text(json.dumps(release))
    return dict(expected_manifest_sha256=sha(root/'release_manifest.json'),
        expected_source_binding=release['source_binding'],expected_tokenizer_sha256=sha(root/'tokenizer.json'),guard=lambda:None)


def test_fresh_content_and_live_mutation(tmp_path):
    root=tmp_path/'hf';kw=fixture(root);live=authenticate_release(root,**kw)
    live.check()
    p=root/'model.safetensors';before=p.read_bytes();p.write_bytes(before)
    with pytest.raises(ValueError,match='changed'):live.check()


@pytest.mark.parametrize('fault',['manifest_sha','binding','tokenizer','payload','extra','missing','symlink','nested','traversal'])
def test_reject_wrong_or_partial_release(tmp_path,fault):
    root=tmp_path/'hf';kw=fixture(root)
    if fault=='manifest_sha':kw['expected_manifest_sha256']='0'*64
    if fault=='binding':kw['expected_source_binding']={'scope':'other'}
    if fault=='tokenizer':kw['expected_tokenizer_sha256']='0'*64
    if fault=='payload':(root/'model.safetensors').write_bytes(b'corrupted')
    if fault=='extra':(root/'unexpected').write_text('extra')
    if fault=='missing':(root/'release_manifest.json').unlink()
    if fault=='symlink':
        p=root/'config.json';p.unlink();p.symlink_to(root/'tokenizer.json')
    if fault=='nested':(root/'subdirectory').mkdir()
    if fault=='traversal':
        p=root/'release_manifest.json';d=json.loads(p.read_text());d['files']['../outside']={'bytes':0,'sha256':'0'*64};p.write_text(json.dumps(d));kw['expected_manifest_sha256']=sha(p)
    with pytest.raises((ValueError,FileNotFoundError)):authenticate_release(root,**kw)


def test_guard_failure_and_changed_during_hash(tmp_path):
    root=tmp_path/'hf';kw=fixture(root)
    def stop():raise RuntimeError('Resource guard failed')
    with pytest.raises(RuntimeError,match='Resource'):authenticate_release(root,**dict(kw,guard=stop))
    calls=[]
    def mutate():
        calls.append(1)
        if len(calls)==3:(root/'config.json').write_text('{"altered":true}')
    with pytest.raises(ValueError):authenticate_release(root,**dict(kw,guard=mutate))


def test_actual_tiny_hf_publisher_compatibility(tmp_path):
    # Actual native->HF weights/numerical comparison/publication; no 3B/GPU claim.
    import importlib.util
    import subprocess
    spec=importlib.util.spec_from_file_location('runtime_fixture',Path(__file__).with_name('test_hf_publication.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    actual_fixture=module.fixture
    from inference.verify_package import verify_stage,publish_stage
    native,tok,stage,cases=actual_fixture(tmp_path)
    validation=verify_stage(stage,native,tok,cases,guard=lambda:None)
    binding={'scope':'actual_tiny_cpu_not_final35k'};root=tmp_path/'hf'
    publish_stage(stage,root,source_binding=binding,validation=validation,guard=lambda:None)
    live=authenticate_release(root,expected_manifest_sha256=sha(root/'release_manifest.json'),
        expected_source_binding=binding,expected_tokenizer_sha256=sha(tok),guard=lambda:None)
    live.check()
    assert live.release['vllm_verified'] is False
    # Same payload is independently consumed by the isolated Python with site
    # packages disabled: the gate must not import either training or vLLM torch.
    contract=json.loads(Path('runs/main_training_35k_plan_v1/serving_environment_v2/contract.json').read_text())
    python=Path(contract['local_root'])/'venv/bin/python'
    args=dict(directory=str(root),expected_manifest_sha256=sha(root/'release_manifest.json'),
        expected_source_binding=binding,expected_tokenizer_sha256=sha(tok))
    code="""import json,sys
from inference.runtime_package import authenticate_release
live=authenticate_release(**json.load(sys.stdin),guard=lambda:None)
live.check()
assert 'torch' not in sys.modules and 'transformers' not in sys.modules
print(json.dumps({'status':'passed_isolated_stdlib_payload_gate','files':len(live.release['files'])}))
"""
    run=subprocess.run([str(python),'-S','-c',code],input=json.dumps(args),capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stderr
    assert json.loads(run.stdout)['status']=='passed_isolated_stdlib_payload_gate'
