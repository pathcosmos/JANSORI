"""Supervised, isolated final-model API server on one exclusive loopback socket."""
import json
import os
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve, identity, atomic_json, sha256_file
from inference.runtime_package import exact_path, authenticate_release
from inference.serving_process import bind_loopback, listener_record, process_ticks


def server_arguments(c):
    return ['--model', str(resolve(c['package'])), '--tokenizer', str(resolve(c['package'])),
        '--served-model-name', c['model_alias'], '--host', '127.0.0.1', '--port', '0',
        '--dtype', 'bfloat16', '--tensor-parallel-size', '1', '--max-model-len', '4096',
        '--max-num-seqs', '12', '--max-num-batched-tokens', '4096', '--gpu-memory-utilization', '0.25',
        '--enforce-eager', '--no-enable-prefix-caching', '--seed', '0', '--generation-config', 'vllm',
        '--no-enable-log-requests', '--disable-uvicorn-access-log']


def run(root):
    root = exact_path(resolve(root))
    c = json.loads((root/'contract.json').read_text())
    if (c['purpose'] != 'final35k_vllm_serving' or resolve(c['root']) != root
            or Path(sys.prefix) != Path(c['venv']) or os.environ.get('CUDA_VISIBLE_DEVICES') != '0'):
        raise ValueError('Wrong serving environment/contract')
    for p,h in {**c['code'], **c['inputs']}.items():
        if sha256_file(resolve(p)) != h:
            raise ValueError('Serving code/input changed: '+p)
    admitted = json.loads((root/'admitted.json').read_text())
    if admitted['contract_id'] != identity(c):
        raise ValueError('Serving admission differs')
    path = root/'server/handle.json'
    deadline = time.monotonic()+30
    while not path.exists():
        if time.monotonic() > deadline:
            raise RuntimeError('Missing server supervision handle')
        time.sleep(.05)
    handle = json.loads(path.read_text())
    if (handle['pid'] != os.getpid() or handle['process']['start_ticks'] != process_ticks(os.getpid())
            or handle['owner'] != os.environ.get('LLM_HORIZON_DIAGNOSTIC_OWNER')):
        raise ValueError('Serving process ownership differs')
    began = time.monotonic()
    def guard():
        if (time.monotonic()-began > c['max_seconds'] or
                shutil.disk_usage(root).free < 200*2**30 or
                shutil.disk_usage(c['local_root']).free < 200*2**30):
            raise RuntimeError('Server startup time/storage budget exceeded')
    live = authenticate_release(resolve(c['package']), expected_manifest_sha256=admitted['manifest_sha256'],
        expected_source_binding=admitted['source_binding'], expected_tokenizer_sha256=c['tokenizer_sha256'], guard=guard)
    from importlib.metadata import version
    versions = {n:version(n) for n in c['versions']}
    if versions != c['versions']:
        raise ValueError('Serving package versions changed')
    import torch
    if torch.cuda.device_count() != 1 or 'H200' not in torch.cuda.get_device_name(0):
        raise ValueError('Require exactly one visible H200')
    torch.set_num_threads(c['threads']); torch.set_num_interop_threads(1)
    from vllm.entrypoints.openai.api_server import run_server_worker, validate_api_server_args
    from vllm.entrypoints.openai.cli_args import make_arg_parser, validate_parsed_serve_args
    from vllm.utils.argparse_utils import FlexibleArgumentParser
    import uvloop
    argv = server_arguments(c)
    args = make_arg_parser(FlexibleArgumentParser()).parse_args(argv)
    validate_parsed_serve_args(args); validate_api_server_args(args)
    live.check()
    with bind_loopback() as sock:
        args.port = sock.getsockname()[1]
        record = listener_record(sock, owner=handle['owner'])
        record.update(contract_id=identity(c), admission_sha256=sha256_file(root/'admitted.json'),
            manifest_sha256=live.manifest_sha256, versions=versions, argv=argv,
            visible_device_count=1, cuda_build=torch.version.cuda, device_name=torch.cuda.get_device_name(0))
        atomic_json(root/'server_execution.json', record)
        uvloop.run(run_server_worker(f'http://127.0.0.1:{args.port}', sock, args))
    live.check()
    atomic_json(root/'server_exit.json', dict(status='server_context_exited', contract_id=identity(c),
        execution_sha256=sha256_file(root/'server_execution.json'), seconds=time.monotonic()-began,
        goal_complete=False))


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--run-dir', required=True)
    run(p.parse_args().run_dir)
