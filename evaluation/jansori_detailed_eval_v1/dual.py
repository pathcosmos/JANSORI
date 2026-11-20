from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack,contextmanager
import json,os,signal,socket,subprocess,time,threading
from pathlib import Path
import requests,pynvml as nv,psutil
from inputs import d,perf_input,pin
root=d.parent/'one_gpu_setup_2gpu_20261116_v1';release=d.parent/'3b_47k_hub_release_v1/jansori-hub-v1'
store=Path((release/'clean-root').read_text().strip())/'ollama-store'
nv.nvmlInit();dev=[nv.nvmlDeviceGetHandleByIndex(i) for i in (0,1)]
@contextmanager
def server(bf):
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    name='vllm' if bf else 'ollama'
    cmd=(['vllm','serve',pin['local_path'],'--port',str(port),'--host','127.0.0.1','--served-model-name','pathcosmos/JANSORI','--load-format','safetensors','--dtype','bfloat16','--max-model-len','4096','--max-num-seqs','2','--max-num-batched-tokens','4096','--num-gpu-blocks-override','512','--gpu-memory-utilization','0.25','--enforce-eager','--no-enable-prefix-caching','--generation-config','vllm','--seed','47000'] if bf else ['ollama','serve'])
    shell='source "$1";source "$2";shift 2;export CUDA_VISIBLE_DEVICES="$EVAL_GPU" OLLAMA_HOST="127.0.0.1:$EVAL_PORT" OLLAMA_MODELS="$EVAL_STORE" VLLM_ATTENTION_BACKEND=FLASH_ATTN VLLM_USE_FLASHINFER_SAMPLER=0 GOMAXPROCS=3;exec "$@"'
    env=dict(os.environ,EVAL_STORE=str(store),EVAL_GPU='0' if bf else '1',EVAL_PORT=str(port))
    with (d/f'dual-{name}.log').open('w') as log:
        proc=subprocess.Popen(['bash','-c',shell,'dual',str(root/'paths.sh'),str(root/('runtime_env.sh' if bf else 'ollama_env.sh')),*cmd],env=env,stdout=log,stderr=log,start_new_session=True)
        try:
            deadline=time.monotonic()+420
            while True:
                assert proc.poll() is None and time.monotonic()<deadline
                try:requests.get(f'http://127.0.0.1:{port}'+('/health' if bf else '/api/version'),timeout=1).raise_for_status();break
                except requests.RequestException:time.sleep(.5)
            yield port,proc.pid
        finally:
            if proc.poll() is None:proc.terminate()
            try:proc.wait(60)
            except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait();raise
            assert proc.returncode==0
def single(port,bf,fmt,prompt):
    payload=dict(prompt=prompt,stream=False)
    if bf:payload.update(model='pathcosmos/JANSORI',max_tokens=32,seed=47000,temperature=0)
    else:payload.update(model='hf.co/pathcosmos/JANSORI:'+fmt,options=dict(num_ctx=4096,num_gpu=99,num_thread=4,num_predict=32,temperature=0,repeat_penalty=1,seed=47000),raw=True,keep_alive=-1)
    tic=time.monotonic();res=requests.post(f'http://127.0.0.1:{port}'+('/v1/completions' if bf else '/api/generate'),json=payload,timeout=120);res.raise_for_status();x=res.json()
    if bf:k,n,text=x['usage']['prompt_tokens'],x['usage']['completion_tokens'],x['choices'][0]['text']
    else:k,n,text=x['prompt_eval_count'],x['eval_count'],x['response']
    return dict(start=tic,end=time.monotonic(),tokens=n,input=k,text=text)
def sample(procs):
    mem=sum(nv.nvmlDeviceGetMemoryInfo(x).used for x in dev)
    pss=0
    for pid in procs:
        parent=psutil.Process(pid)
        for p in [parent,*parent.children(recursive=True)]:
            try:pss+=p.memory_full_info().pss
            except psutil.NoSuchProcess:pass
    return mem,pss
with server(True) as (a,pa),server(False) as (b,pb),(d/'dual.jsonl').open('x') as f:
    for fmt in ['Q8_0','Q4_K_M']:
        for i in range(5):
            prompt=perf_input(332100+i,1024);base=None
            for mode in (['seq','par'] if i%2==0 else ['par','seq']):
                # Evict Ollama's last-prefix reuse equally before each warm timed pair.
                reset=perf_input(338100+i,1024)
                single(b,False,fmt,reset);single(a,True,fmt,reset)
                samples=[sample([pa,pb])];stop=threading.Event()
                def monitor():
                    while not stop.wait(.5):samples.append(sample([pa,pb]))
                worker=threading.Thread(target=monitor);worker.start()
                try:
                    if mode=='seq':x=single(a,True,fmt,prompt);y=single(b,False,fmt,prompt)
                    else:
                        with ThreadPoolExecutor(max_workers=2) as pool:
                            v=pool.submit(single,a,True,fmt,prompt);q=pool.submit(single,b,False,fmt,prompt);x,y=v.result(),q.result()
                finally:stop.set();worker.join()
                samples.append(sample([pa,pb]))
                assert all(z['input']==1024 and z['tokens']==32 for z in (x,y))
                if base is None:base=(x['text'],y['text'])
                equal=[base[0]==x['text'],base[1]==y['text']]
                f.write(json.dumps(dict(fmt=fmt,i=i,mode=mode,paired_equal=equal,wall=max(x['end'],y['end'])-min(x['start'],y['start']),overlap=max(0,min(x['end'],y['end'])-max(x['start'],y['start'])),combined_vram=max(m for m,_ in samples),pss=max(p for _,p in samples),bf16=x,quant=y),ensure_ascii=False)+'\n');f.flush()
