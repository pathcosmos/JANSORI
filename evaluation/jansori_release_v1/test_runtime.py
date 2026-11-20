"""Exercise the published model ID from a standalone working directory."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import urllib.request

import psutil

p=argparse.ArgumentParser()
p.add_argument('backend',choices=['ollama','vllm'])
p.add_argument('run',type=int,choices=[1,2,3])
args=p.parse_args()
out=Path(__file__).resolve().parent
scratch=Path((out/'clean-root').read_text().strip())
cwd=scratch/'cwd'
repo='pathcosmos/JANSORI'
rev=json.loads((out/'commit.json').read_text())['commit']
prior=json.loads((out.parent/'nfs-release-v1/evidence/fixed-generations.json').read_text())['outputs']
contract=json.loads((out.parent/'serving-bench-v1/contract.json').read_text())
work=out/f'{args.backend}-{args.run}'
work.mkdir(exist_ok=False)
save=lambda name,value:(work/name).write_text(json.dumps(value,ensure_ascii=False,indent=2))
env=os.environ.copy()
for key in ('HF_TOKEN','HUGGING_FACE_HUB_TOKEN','HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_HUB_CACHE','HUGGINGFACE_HUB_CACHE','TRANSFORMERS_CACHE','PYTHONPATH','HF_ENDPOINT','HF_TOKEN_PATH','VLLM_USE_MODELSCOPE'):
    env.pop(key,None)
env.update(HOME=str(scratch/'home'),XDG_CACHE_HOME=str(scratch/'xdg'),HF_HOME=str(scratch/'hf'),HF_XET_CACHE=str(scratch/'xet'),HF_HUB_DISABLE_IMPLICIT_TOKEN='1',VLLM_CACHE_ROOT=str(scratch/'vllm-cache'),VLLM_HOST_IP='127.0.0.1',OLLAMA_MODELS=str(scratch/'ollama-store'),CUDA_VISIBLE_DEVICES='1' if args.backend=='ollama' else '0',GOMAXPROCS='3',VLLM_NO_USAGE_STATS='1',TOKENIZERS_PARALLELISM='false',HF_HUB_DISABLE_TELEMETRY='1')
dev=contract['devices'][1 if args.backend=='ollama' else 0]
if args.backend=='vllm':
    snapshot=json.loads((out/'public-snapshot.json').read_text())
    cache=Path(snapshot['local_path']).parents[2]
    assert cache.is_relative_to(scratch) and snapshot['commit']==rev and cache.name=='hub'
    env.update(HF_HOME=str(cache.parent),HF_HUB_CACHE=str(cache))
def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));return s.getsockname()[1]
port=free_port()
env['OLLAMA_HOST']=f'127.0.0.1:{port}'
def http(path,body=None,timeout=180):
    req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=timeout) as resp:
        data=resp.read();return json.loads(data) if data else {}
@contextmanager
def serve():
    if args.backend=='ollama':
        cmd=['ollama','serve']
    else:
        cmd=['vllm','serve',repo,'--revision',rev,'--tokenizer-revision',rev,'--load-format','safetensors','--dtype','bfloat16','--tensor-parallel-size','1','--max-model-len','4096','--host','127.0.0.1','--port',str(port)]
        if args.run==3:
            cmd+=['--max-num-seqs','2','--max-num-batched-tokens','4096','--gpu-memory-utilization','0.25','--num-gpu-blocks-override','512','--enforce-eager','--no-enable-prefix-caching','--generation-config','vllm','--seed','47000']
            env.update(VLLM_ATTENTION_BACKEND='FLASH_ATTN',VLLM_USE_FLASHINFER_SAMPLER='0')
    with (work/'server.log').open('wb') as log:
        proc=subprocess.Popen(cmd,env=env,cwd=cwd,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        receipt=dict(command=cmd,revision=rev,pid=proc.pid,physical_device=dev,port=port)
        try:
            deadline=time.monotonic()+1200
            while True:
                if proc.poll() is not None:raise RuntimeError(f'early exit {proc.returncode}')
                try:http('/api/version' if args.backend=='ollama' else '/health',timeout=2);break
                except OSError:
                    if time.monotonic()>deadline:raise TimeoutError('startup')
                    time.sleep(1)
            yield proc
        finally:
            if proc.poll() is None: proc.terminate()
            try:proc.wait(70)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid,signal.SIGKILL);proc.wait();receipt['forced_kill']=True
            with socket.socket() as s:
                receipt['port_closed']=s.connect_ex(('127.0.0.1',port))!=0
            receipt['rc']=proc.returncode
            save('lifecycle.json',receipt)
            if not receipt['port_closed'] or receipt.get('forced_kill'):raise RuntimeError('unclean shutdown')
def api_generate(fmt,name,prompt,limit=64,raw=False):
    if fmt=='BF16':
        r=http('/v1/completions',dict(model=name,prompt=prompt,max_tokens=limit,temperature=0,seed=47000))
        return r['choices'][0]['text'],r['usage']['completion_tokens']
    opts=dict(num_gpu=99,num_thread=4,num_ctx=4096,num_predict=limit,temperature=0,repeat_penalty=1,seed=47000)
    r=http('/api/generate',dict(model=name,prompt=prompt,raw=raw,stream=False,keep_alive='2m',options=opts))
    assert r['done']
    return r['response'],r['eval_count']
with serve() as proc:
    formats=['BF16'] if args.backend=='vllm' else ['Q8_0','Q4_K_M']
    for fmt in formats:
        name=repo if fmt=='BF16' else 'hf.co/'+repo+':'+fmt
        if fmt=='BF16':
            assert http('/v1/models')['data'][0]['id']==repo
        elif args.run==1:
            cli=['ollama','run',name,'한국의 수도는']
            with (work/(fmt+'-cli.out')).open('wb') as a,(work/(fmt+'-cli.err')).open('wb') as b:
                subprocess.run(cli,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=a,stderr=b,check=True,timeout=1200)
            assert (work/(fmt+'-cli.out')).stat().st_size>2
        if fmt!='BF16':
            show=http('/api/show',dict(model=name));assert show['template'].strip()=='{{ .Prompt }}',repr(show['template'])
            assert show['details']['quantization_level']==fmt
            save(fmt+'-identity.json',dict(template=show['template'],details=show['details'],context=show.get('model_info',{}).get('llama.context_length')))
        text,count=api_generate(fmt,name,'한국의 수도는',32);assert text and 0<count<=32
        if fmt!='BF16':
            assert api_generate(fmt,name,'한국의 수도는',32,True)==(text,count)
            live=http('/api/ps')['models'];assert len(live)==1 and live[0]['size_vram']>0
            lines=(work/'server.log').read_text(errors='replace').lower().splitlines()
            assert any('using device cuda' in l and dev['bus'] in l for l in lines)
            save(fmt+'-device.json',live)
        else:
            smi=subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,gpu_uuid','--format=csv,noheader'],text=True)
            smem=subprocess.check_output(['nvidia-smi','--query-gpu=index,uuid,memory.used','--format=csv,noheader,nounits'],text=True)
            own={x.pid for x in psutil.Process(proc.pid).children(recursive=True)}|{proc.pid}
            save('device.json',dict(visible=env['CUDA_VISIBLE_DEVICES'],own_pids=sorted(own),compute_apps=smi,memory_by_uuid=smem))
            assert any(dev['uuid'] in l and int(l.split(',')[-1])>5000 for l in smem.splitlines())
        rows=[]
        for ref in prior[fmt]:
            txt,n=api_generate(fmt,name,ref['prompt'])
            rows.append(dict(prompt=ref['prompt'],text=txt,tokens=n,exact_to_nfs=(txt,n)==(ref['text'],ref['tokens'])))
        save(fmt+'-generation.json',dict(example=dict(text=text,tokens=count),cases=rows))
        if fmt!='BF16':
            http('/api/generate',dict(model=name,stream=False,keep_alive=0))
            until=time.monotonic()+30
            while http('/api/ps')['models']:
                if time.monotonic()>until:raise TimeoutError('unload')
                time.sleep(.2)
print(args.backend,args.run,'finished',flush=True)
