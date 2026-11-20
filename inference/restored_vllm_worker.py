"""Isolated, controller-owned final HF inference worker; not a public server."""
import json
import os
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve, identity, atomic_json, sha256_file
from inference.runtime_package import authenticate_release, exact_path


def run(root):
    root = exact_path(resolve(root))
    c = json.loads((root/'contract.json').read_text())
    if (c['purpose'] != 'final57k_vllm_runtime' or resolve(c['root']) != root
            or Path(sys.prefix) != Path(c['venv']) or os.environ.get('CUDA_VISIBLE_DEVICES') != '0'):
        raise ValueError('Wrong isolated runtime/worker contract')
    for p,h in {**c['code'], **c['inputs']}.items():
        if sha256_file(resolve(p)) != h:
            raise ValueError('Runtime code/input changed: '+p)
    admission = json.loads((root/'admitted.json').read_text())
    if admission['contract_id'] != identity(c):
        raise ValueError('Runtime admission contract changed')
    handle = root/'supervision/handle.json'; deadline = time.monotonic()+30
    while not handle.exists():
        if time.monotonic()>deadline:raise RuntimeError('Missing supervised worker handle')
        time.sleep(.05)
    h = json.loads(handle.read_text())
    ticks = int(Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19])
    if (h['pid'] != os.getpid() or h['process']['start_ticks'] != ticks
            or h['owner'] != os.environ.get('LLM_HORIZON_DIAGNOSTIC_OWNER')):
        raise ValueError('Worker ownership changed')
    started = time.monotonic()
    def guard():
        if (time.monotonic()-started>c['max_seconds'] or
                shutil.disk_usage(root).free<200*2**30 or
                shutil.disk_usage(c['local_root']).free<200*2**30):
            raise RuntimeError('Runtime worker time/storage guard')
    live = authenticate_release(resolve(c['package']),
        expected_manifest_sha256=admission['manifest_sha256'],
        expected_source_binding=admission['source_binding'],
        expected_tokenizer_sha256=c['tokenizer_sha256'],guard=guard)
    from importlib.metadata import version
    versions = {n:version(n) for n in c['versions']}
    if versions != c['versions']:raise ValueError('Isolated package versions changed')
    import torch
    if torch.cuda.device_count()!=1:raise ValueError('Require exactly one visible GPU')
    torch.set_num_threads(c['threads']);torch.set_num_interop_threads(1)
    name = torch.cuda.get_device_name(0)
    if 'H200' not in name:raise ValueError('Unexpected inference device')
    from tokenizers import Tokenizer
    from vllm import LLM, SamplingParams
    from inference.vllm_validation import build_cases, check_engine_outputs
    raw = Tokenizer.from_file(str(resolve(c['tokenizer'])))
    prompts = [json.loads(s) for s in resolve(c['prompts']).read_text().splitlines() if s.strip()]
    cases = build_cases(raw,prompts)
    atomic_json(root/'worker_execution.json',dict(pid=os.getpid(),start_ticks=ticks,owner=h['owner'],
        contract_id=identity(c),admission_sha256=sha256_file(root/'admitted.json'),versions=versions,
        device_name=name,visible_device_count=1,cuda_build=torch.version.cuda,
        package_manifest_sha256=live.manifest_sha256))
    atomic_json(root/'worker_state.json',dict(case='construct_engine',unix=time.time()))
    live.check()
    engine = LLM(model=str(live.directory), tokenizer=str(live.directory), **c['engine'])
    try:
        atomic_json(root/'worker_state.json',dict(case='actual_gpu_generate',unix=time.time()))
        result = check_engine_outputs(engine,raw,cases,SamplingParams,guard=live.check)
        live.check()
    finally:
        # Pinned v0.11.2 V1 EngineCoreClient API. Shut down its processes before
        # the supervising launcher verifies that no owned descendants remain.
        engine.llm_engine.engine_core.shutdown()
    atomic_json(root/'worker_result.json',dict(status='passed_final57k_vllm_worker_pending_review',
        cases=cases,output_checks=result,execution_sha256=sha256_file(root/'worker_execution.json'),
        manifest_sha256=live.manifest_sha256,seconds=time.monotonic()-started,
        actual_gpu_inference_executed=True,quality_success_asserted=False,ollama_verified=False,goal_complete=False))
    # Engine descendants must exit too; the owning supervisor verifies that.
    atomic_json(root/'worker_state.json',dict(case='inference_done_await_process_exit',unix=time.time()))


if __name__ == '__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run-dir',required=True)
    run(p.parse_args().run_dir)
