"""Build one isolated, CPU-only inference environment in the shared local pool.

No global pip changes, training signals, model downloads or GPU inference.
The resulting setup is a candidate until actual final-model serving passes.
"""
import argparse
import importlib.metadata
import json
import os
import re
import signal
from pathlib import Path
import shutil
import sys
import time
from urllib.parse import urlparse

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve,atomic_json,sha256_file,identity
from scripts.tiered_checkpoint_retention import exact_path,size_tree
from scripts.horizon_probe_process import run_command

GIB=2**30
SPEC=['vllm==0.11.2','transformers==4.57.6','torch==2.9.0+cu128',
      'torchaudio==2.9.0+cu128','torchvision==0.24.0+cu128']
BASE_PACKAGES=['torch','transformers','tokenizers','safetensors','huggingface-hub']


def wheel_lock(resolution):
    lines=[];wheels=[];versions={}
    for item in resolution['install']:
        info=item['download_info'];url=info['url'];parsed=urlparse(url)
        digest=info['archive_info']['hashes']['sha256']
        name=item['metadata']['name'];version=item['metadata']['version']
        normalized=re.sub(r'[-_.]+','-',name).lower()
        allowed=parsed.hostname in ('files.pythonhosted.org','download.pytorch.org','download-r2.pytorch.org')
        allowed=allowed or (parsed.hostname=='pypi.nvidia.com' and normalized.startswith('nvidia-'))
        if (not allowed or parsed.scheme!='https' or parsed.username or parsed.password or
                not parsed.path.endswith('.whl') or not re.fullmatch(r'[0-9a-f]{64}',digest) or
                not re.fullmatch(r'[A-Za-z0-9_.-]+',name) or any(c.isspace() for c in url)):
            raise ValueError('Expected a hashed public binary wheel: '+normalized)
        if normalized in versions:raise ValueError('Duplicate wheel')
        versions[normalized]=version
        lines.append(f'{name} @ {url} --hash=sha256:{digest}')
        wheels.append(dict(name=name,version=version,url=url,sha256=digest))
    for spec in SPEC:
        name,version=spec.split('==')
        if versions.get(name)!=version:raise ValueError('Resolution differs from pinned candidate: '+name)
    return lines,wheels


def main(a):
    root=exact_path(resolve(a.run_dir))
    if root.exists():raise ValueError('Preserve existing environment preparation attempt')
    plan=resolve('runs/main_training_35k_plan_v1/authorized_transition8000_plan.json')
    pinned=json.loads(plan.read_text());draft=json.loads(resolve(pinned['draft']).read_text())
    bindings={**pinned['code'],**pinned['inputs']}
    for p,h in bindings.items():
        if sha256_file(resolve(p))!=h:raise ValueError('Active transition binding differs: '+p)
    pool=exact_path(Path(draft['space_reservations']['local_temp_paths'][0]))
    if not re.fullmatch(r'serving_vllm0112_v[1-9][0-9]*',a.local_name):raise ValueError('Invalid isolated namespace')
    local=pool/a.local_name;venv=local/'venv'
    if local.exists():raise ValueError('Preserve existing isolated environment')
    prior=None
    if a.resolution_from:
        path=exact_path(resolve(a.resolution_from));payload=path.read_bytes()
        resolution=json.loads(payload);wheel_lock(resolution)
        prior=dict(path=str(path),sha256=sha256_file(path))
    base={name:importlib.metadata.version(name) for name in BASE_PACKAGES}
    root.mkdir(parents=True);local.mkdir()
    for name in ('tmp','cache'):(local/name).mkdir()
    atomic_json(root/'contract.json',dict(purpose='isolated_vllm_candidate_preparation',spec=SPEC,
        local_root=str(local),base_versions=base,transition_plan_sha256=sha256_file(plan),
        local_cap_gib=64,shared_temp_gib=1024,durable_cap_mib=128,max_pss_gib=16,max_seconds=2400,
        code_sha256=sha256_file(Path(__file__)),reused_resolution=prior,gpu_execution=False,external_upload=False))
    if prior:
        if sha256_file(path)!=prior['sha256']:raise ValueError('Resolution changed during admission')
        (root/'resolution.json').write_bytes(payload)
    (root/'pid').write_text(str(os.getpid())+'\n');began=time.monotonic();metrics=[];peak_bytes=0
    env={k:v for k,v in os.environ.items() if not k.startswith('PIP_') and k not in ('PYTHONPATH','VIRTUAL_ENV')}
    env.update(CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',MKL_NUM_THREADS='2',
        TOKENIZERS_PARALLELISM='false',PIP_CONFIG_FILE='/dev/null',PIP_DISABLE_PIP_VERSION_CHECK='1',
        TMPDIR=str(local/'tmp'),XDG_CACHE_HOME=str(local/'cache'),HF_HOME=str(local/'cache/hf'),
        HF_HUB_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',VLLM_NO_USAGE_STATS='1',DO_NOT_TRACK='1',
        LD_LIBRARY_PATH='/usr/local/nvidia/lib:/usr/local/nvidia/lib64',PYTHONUNBUFFERED='1')
    def guard(owner,elapsed,pss):
        nonlocal peak_bytes
        used=size_tree(local);peak_bytes=max(peak_bytes,used)
        temp=sum(size_tree(Path(p)) for p in draft['space_reservations']['local_temp_paths'])
        reservations={str(resolve(draft['intended_run'])):draft['durable_budget_bytes'],**draft['space_reservations']['nfs']}
        future=sum(max(0,b-size_tree(Path(p))) for p,b in reservations.items())
        incoming=int(draft['expected_checkpoint_bytes']*1.1)+GIB
        if (time.monotonic()-began>2400 or pss>16*GIB or used>64*GIB or temp>1024*GIB or
                size_tree(root)>128*2**20 or shutil.disk_usage(local).free<1024*GIB-temp+incoming+200*GIB or
                shutil.disk_usage(root).free<future+200*GIB+128*2**20):
            raise RuntimeError('Isolated environment resource/shared storage budget exceeded')
        atomic_json(root/'state.json',dict(phase='running',case=guard.case,worker_pid=owner.proc.pid,
            elapsed_seconds=time.monotonic()-began,peak_local_bytes=peak_bytes,worker_peak_pss_bytes=pss))
    def execute(name,command):
        path=root/name;path.mkdir()
        guard.case=name
        metrics.append(run_command(['taskset','-c','4-7',*command],path,env,guard,interval=2))
    try:
        execute('hardware',[sys.executable,'scripts/hardware_profile.py','--output',str(root/'hardware.json')])
        execute('venv',[sys.executable,'-m','venv','--copies',str(venv)])
        py=str(venv/'bin/python')
        common=[py,'-m','pip','install','--no-cache-dir','--only-binary=:all:','--retries','1','--timeout','30',
                '--index-url','https://pypi.org/simple','--extra-index-url','https://download.pytorch.org/whl/cu128']
        if not prior:execute('resolve',[*common,'--dry-run','--report',str(root/'resolution.json'),*SPEC])
        resolution=json.loads((root/'resolution.json').read_text());lines,urls=wheel_lock(resolution)
        (root/'requirements.lock').write_text('\n'.join(lines)+'\n')
        atomic_json(root/'wheels.json',urls)
        execute('install',[py,'-m','pip','install','--no-cache-dir','--require-hashes','--only-binary=:all:',
            '--retries','1','--timeout','30','--no-deps','-r',str(root/'requirements.lock'),'--report',str(root/'installation.json')])
        execute('pip_check',[py,'-m','pip','check'])
        code="""import json,sys,torch,transformers,tokenizers,vllm
from importlib.metadata import version
from pathlib import Path
from tokenizers import Tokenizer
from transformers import PreTrainedTokenizerFast
p=Path('tokenizer/candidate_v1/tokenizer.json');base=Tokenizer.from_file(str(p))
tok=PreTrainedTokenizerFast(tokenizer_file=str(p),bos_token='<|bos|>',eos_token='<|eos|>',pad_token='<|pad|>',clean_up_tokenization_spaces=False)
for s in ['한국어 테스트','Hello world','123456 + 001 = ?', 'def f(x):\\n    return x**2','😀 é العربية','<|bos|>x<|eos|>']:
 assert tok.encode(s)==base.encode(s,add_special_tokens=False).ids
 assert tok.decode(base.encode(s).ids,skip_special_tokens=False)==base.decode(base.encode(s).ids,skip_special_tokens=False)
assert not torch.cuda.is_initialized()
report=dict(python=sys.version,prefix=sys.prefix,versions={n:version(n) for n in ['torch','transformers','tokenizers','vllm','flashinfer-python']},cuda_build=torch.version.cuda,cuda_initialized=False,tokenizer_probes=6,gpu_inference_verified=False)
Path(sys.argv[1]).write_text(json.dumps(report,indent=2)+'\\n')
print(json.dumps(report))
"""
        (root/'import_probe.py').write_text(code)
        execute('imports',[py,str(root/'import_probe.py'),str(root/'import_report.json')])
        if {name:importlib.metadata.version(name) for name in BASE_PACKAGES}!=base:
            raise ValueError('Base training environment changed')
        for p,h in bindings.items():
            if sha256_file(resolve(p))!=h:raise ValueError('Protected transition code/input changed')
        report=dict(status='prepared_isolated_vllm_cpu_only_gpu_pending',local_root=str(local),venv=str(venv),
            spec=SPEC,import_report_sha256=sha256_file(root/'import_report.json'),lock_sha256=sha256_file(root/'requirements.lock'),
            base_versions_preserved=base,peak_local_bytes=peak_bytes,final_local_bytes=size_tree(local),supervision=metrics,
            elapsed_seconds=time.monotonic()-began,gpu_inference_verified=False,goal_complete=False)
        atomic_json(root/'report.json',report)
        atomic_json(root/'.done',dict(report_sha256=sha256_file(root/'report.json'),contract_sha256=sha256_file(root/'contract.json')))
        atomic_json(root/'state.json',dict(phase='complete',case='cpu_setup_passed_gpu_pending'))
    except BaseException as exc:
        atomic_json(root/'state.json',dict(phase='error',reason=repr(exc),partial_outputs_preserved=True))
        raise


if __name__=='__main__':
    def interrupted(signum,frame):raise InterruptedError('Environment preparation interrupted: '+str(signum))
    signal.signal(signal.SIGTERM,interrupted)
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run-dir',required=True)
    p.add_argument('--local-name',default='serving_vllm0112_v1');p.add_argument('--resolution-from')
    main(p.parse_args())
