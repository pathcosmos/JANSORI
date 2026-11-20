from pathlib import Path
import json,os,signal,socket,subprocess,time,requests
from grade import code_result
d=Path(__file__).resolve().parent
r=d.parent/'3b_47k_hub_release_v1'
snapshot=json.loads((r/'jansori-hub-v1/public-snapshot.json').read_text())['local_path']
items=[q for q in json.loads((d/'quality-inputs.json').read_text()) if q['tag']=='code']
examples='def add_one(x):\n    """Return x plus one."""\n    return x + 1\n\ndef smaller(a, b):\n    """Return the smaller of a and b."""\n    return min(a, b)\n\n'
profiles=d.parent/'one_gpu_setup_2gpu_20261116_v1'
for engine,formats in [('ollama',['Q8_0','Q4_K_M']),('vllm',['BF16'])]:
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    api='http://127.0.0.1:'+str(port)
    store=Path((r/'jansori-hub-v1/clean-root').read_text().strip())/'ollama-store'
    env=dict(os.environ,EVAL_STORE=str(store),EVAL_PORT=str(port))
    profile='runtime_env.sh' if engine=='vllm' else 'ollama_env.sh'
    cmd=(['vllm','serve',snapshot,'--port',str(port),'--host','127.0.0.1','--served-model-name','pathcosmos/JANSORI','--load-format','safetensors','--dtype','bfloat16','--max-model-len','4096','--max-num-seqs','2','--max-num-batched-tokens','4096','--num-gpu-blocks-override','512','--gpu-memory-utilization','0.25','--enforce-eager','--no-enable-prefix-caching','--generation-config','vllm','--seed','47000'] if engine=='vllm' else ['ollama','serve'])
    shell='source "$1"; source "$2"; shift 2; export CUDA_VISIBLE_DEVICES=1 OLLAMA_MODELS="$EVAL_STORE" OLLAMA_HOST="127.0.0.1:$EVAL_PORT" VLLM_ATTENTION_BACKEND=FLASH_ATTN VLLM_USE_FLASHINFER_SAMPLER=0 GOMAXPROCS=3; exec "$@"'
    with (d/('scaffold-'+engine+'.log')).open('w') as log:
        proc=subprocess.Popen(['bash','-c',shell,'scaffold',str(profiles/'paths.sh'),str(profiles/profile),*cmd],env=env,stdout=log,stderr=log,start_new_session=True)
        try:
            ready=time.monotonic()+360
            while True:
                assert proc.poll() is None and time.monotonic()<ready
                try:requests.get(api+('/health' if engine=='vllm' else '/api/version'),timeout=1).raise_for_status();break
                except requests.RequestException:time.sleep(.5)
            for fmt in formats:
                dest=d/fmt/'scaffold.jsonl'
                with dest.open('x') as f:
                    for item in items:
                        prompt=examples+item['prompt']+'    return'
                        if engine=='vllm':
                            res=requests.post(api+'/v1/completions',json=dict(model='pathcosmos/JANSORI',prompt=prompt,max_tokens=96,temperature=0,seed=47000),timeout=90);res.raise_for_status();text=res.json()['choices'][0]['text']
                        else:
                            opts=dict(num_gpu=99,num_ctx=4096,num_predict=96,temperature=0,repeat_penalty=1,seed=47000,num_thread=4)
                            res=requests.post(api+'/api/generate',json=dict(model='hf.co/pathcosmos/JANSORI:'+fmt,prompt=prompt,raw=True,options=opts,stream=False,keep_alive='2m'),timeout=120);res.raise_for_status();text=res.json()['response']
                        g=code_result(item,'    return'+text)
                        f.write(json.dumps(dict(id=item['id'],prompt=prompt,generated=text,**g),ensure_ascii=False)+'\n')
        finally:
            if proc.poll() is None:proc.terminate()
            try:proc.wait(65)
            except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait();raise
        assert proc.returncode==0
