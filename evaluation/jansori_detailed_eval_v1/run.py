import argparse,concurrent.futures,json,os,random,resource,threading,time
from pathlib import Path
import psutil,pynvml as nv,requests
from inputs import d,t,perf_input,items
arg=argparse.ArgumentParser()
arg.add_argument('fmt',choices=['BF16','Q8_0','Q4_K_M']);arg.add_argument('--phase',choices=['all','quality','perf'],default='all')
args=arg.parse_args()
fmt=args.fmt; bf=fmt=='BF16'
engine='vllm' if bf else 'ollama'
port=(d/(engine+'.port')).read_text()
url='http://127.0.0.1:'+port
model='pathcosmos/JANSORI' if bf else 'hf.co/pathcosmos/JANSORI:'+fmt
out=d/fmt;out.mkdir(exist_ok=True)
pid=int((d/(engine+'.pid')).read_text())
assert psutil.pid_exists(pid)
nv.nvmlInit();gpu=nv.nvmlDeviceGetHandleByIndex(0)
def dump(path,obj):path.write_text(json.dumps(obj,ensure_ascii=False,indent=1))
def append(path,obj):
    with path.open('a') as f:f.write(json.dumps(obj,ensure_ascii=False)+'\n')
def raw(path):return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []
class Recorder:
    def __init__(self):
        self.lock=threading.Lock();self.rows=[];self.done=threading.Event();self.thread=threading.Thread(target=self.loop,daemon=True)
    def take(self):
        with self.lock:
            procs=[psutil.Process(pid)];procs+=procs[0].children(recursive=True)
            pss=0
            for p in procs:
                try:pss+=p.memory_full_info().pss
                except psutil.NoSuchProcess:continue
            stat=dict(l.split() for l in Path('/sys/fs/cgroup/memory.stat').read_text().splitlines())
            row=dict(ts=time.monotonic(),vram=nv.nvmlDeviceGetMemoryInfo(gpu).used,pss=pss,ram=int(Path('/sys/fs/cgroup/memory.current').read_text()),file_cache=int(stat['file']))
            self.rows.append(row);append(out/'samples.jsonl',row)
            return row
    def loop(self):
        while not self.done.wait(.5):self.take()
def infer(text,limit,stream,quality=False):
    opts=dict(num_gpu=99,num_thread=4,num_ctx=4096,num_predict=limit,temperature=0,repeat_penalty=1,seed=47000)
    route='/v1/completions' if bf else '/api/generate'
    req=dict(model=model,prompt=text,stream=stream)
    if bf:
        req.update(max_tokens=limit,temperature=0,seed=47000)
        if stream:req['stream_options']={'include_usage':True}
    else:req.update(raw=True,keep_alive=-1,options=opts)
    tic=time.monotonic(); first=None;parts=[]; server={};n=None;k=None;reason=None;error=None
    with requests.post(url+route,json=req,stream=stream,timeout=(10,300)) as r:
        if r.status_code!=200:raise ValueError(r.text[:500])
        if stream:
            for line in r.iter_lines(chunk_size=1):
                if not line:continue
                if bf:
                    if not line.startswith(b'data: '):continue
                    if line[6:]==b'[DONE]':continue
                    res=json.loads(line[6:]);choices=res.get('choices',[])
                    chunk=choices[0]['text'] if choices else ''
                    if choices and choices[0].get('finish_reason'):reason=choices[0]['finish_reason']
                    if res.get('usage'):
                        n=res['usage']['completion_tokens'];k=res['usage']['prompt_tokens']
                else:
                    res=json.loads(line);chunk=res.get('response','')
                    if res.get('error'):
                        error=res['error'];reason='error';break
                    if res.get('done'):
                        n=res.get('eval_count',0);k=res.get('prompt_eval_count',0);reason=res.get('done_reason')
                        server={key:res.get(key) for key in ['prompt_eval_duration','eval_duration','load_duration']}
                if chunk and first is None:first=time.monotonic()
                parts.append(chunk)
            result=''.join(parts)
        else:
            res=r.json()
            if bf:
                result=res['choices'][0]['text']; reason=res['choices'][0]['finish_reason'];n=res['usage']['completion_tokens'];k=res['usage']['prompt_tokens']
            else:
                result=res['response'];reason=res.get('done_reason');n=res.get('eval_count',0);k=res.get('prompt_eval_count',0)
    end=time.monotonic()
    return dict(text=result,n=n,k=k,reason=reason,error=error,start=tic,end=end,latency=end-tic,ttft=first-tic if first else None,server=server)
ready=time.monotonic()+400
while True:
    try:
        r=requests.get(url+('/health' if bf else '/api/version'),timeout=2);r.raise_for_status();break
    except requests.RequestException:
        if time.monotonic()>ready:raise
        time.sleep(.4)
warm=infer(' red blue green',4,False)
offset=warm['k']-3
assert offset in (0,1),warm
dump(out/'calibration.json',dict(model=model,offset=offset,probe=warm,gpu=str(nv.nvmlDeviceGetUUID(gpu)),pid=pid))
if not bf:
    info=requests.post(url+'/api/show',json=dict(model=model)).json();assert info['details']['quantization_level']==fmt and info['template'].strip()=='{{ .Prompt }}'
    ps=requests.get(url+'/api/ps').json()['models'];assert len(ps)==1 and ps[0]['size_vram']>0
    dump(out/'device.json',ps)
if args.phase in ('all','quality'):
    qp=out/'quality.jsonl';have={x['id'] for x in raw(qp)}
    for item in items:
        if item['id'] in have:continue
        limit={'fact':24,'reading':24,'story':64,'math':16,'code':96,'regression':64}[item['tag']]
        row=infer(item['prompt'],limit,False,True);append(qp,dict(id=item['id'],limit=limit,**row))
if args.phase in ('all','perf'):
    path=out/'perf.jsonl';have={x['key'] for x in raw(path)}
    rec=Recorder();rec.thread.start();rec.take()
    # Single-user full matrix; load sweep at a single reference workload so queueing has a fixed token budget.
    conditions=[(k,n,1) for k in [128,1024,3072] for n in [32,128]]+[(3968,128,1),(1024,32,2),(1024,32,4)]
    order=random.Random(47000)
    try:
        for repeat in range(5):
            order.shuffle(conditions)
            for length,limit,conc in conditions:
                key=f'{length}/{limit}/{conc}/{repeat}'
                if key in have:continue
                seeds=[47000+repeat*710003+length*37+limit*19+conc*7+i for i in range(conc)]
                prompts=[perf_input(s,length-offset) for s in seeds]
                before=rec.take()
                with concurrent.futures.ThreadPoolExecutor(max_workers=conc) as pool:
                    results=list(pool.map(lambda p:infer(p,limit,True),prompts))
                after=rec.take()
                samples=[s for s in rec.rows if before['ts']<=s['ts']<=after['ts']]
                for i,row in enumerate(results):
                    row['prompt_seed']=seeds[i]
                    if row['error']:continue
                    if not row['n'] or row['ttft'] is None:raise ValueError(('empty',key,row))
                    if bf and row['k']!=length:raise ValueError(('prompt length',key,row['k']))
                append(path,dict(key=key,repeat=repeat,length=length,limit=limit,concurrency=conc,results=results,samples=len(samples),peak={k:max(s[k] for s in samples) for k in ['vram','pss','ram','file_cache']}))
    finally:rec.done.set();rec.thread.join()
print(fmt,args.phase,'complete',flush=True)
