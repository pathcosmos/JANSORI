from pathlib import Path
import hashlib,json,os,subprocess,sys
import numpy as np
from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer
d=Path(__file__).resolve().parent
r=d.parent/'3b_47k_hub_release_v1'
snap=Path(json.loads((r/'jansori-hub-v1/public-snapshot.json').read_text())['local_path'])
tok=Tokenizer.from_file(str(snap/'tokenizer.json'))
sha='a5ea15e3ac77ed694b79f6204eb31889a2ba989f'
path=Path(hf_hub_download('skt/kobest_v1','boolq/test.jsonl',repo_type='dataset',revision=sha,token=False,local_files_only=True))
data=[json.loads(x) for x in path.read_text().splitlines()]
options=[' 거짓',' 참']
choice=[tok.encode(v).ids for v in options];assert all(len(v)==1 for v in choice)
ids=[];qs=[]
for idx,row in enumerate(data):
    prompt=f"지문: {row['paragraph']}\n질문: {row['question']}\n지문에 비추어 참 또는 거짓으로 답한다.\n답:"
    seq=tok.encode(prompt).ids
    assert len(seq)<4096 and all(tok.encode(prompt+w).ids==seq+v for w,v in zip(options,choice))
    ids.append(seq)
    qs.append(dict(index=idx,label=int(row['label']),token_sha=hashlib.sha256(np.asarray(seq,dtype='<i4').tobytes()).hexdigest()))
assert len(qs)==1404
(d/'boolq-contract.json').write_text(json.dumps(dict(revision=sha,dataset='skt/kobest_v1',split='boolq/test.jsonl',dataset_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),license='CC-BY-SA-4.0',options=options,option_ids=choice,items=qs),ensure_ascii=False))
fmt=sys.argv[1];out=d/fmt/'boolq.jsonl'
def emit(f,i,scores):
    assert np.isfinite(scores).all()
    f.write(json.dumps(dict(index=i,label=qs[i]['label'],logp=scores,pred=int(scores[1]>scores[0])))+'\n')
if fmt=='BF16':
    import torch
    from transformers import AutoModelForCausalLM
    model=AutoModelForCausalLM.from_pretrained(snap,torch_dtype=torch.bfloat16,local_files_only=True,attn_implementation='sdpa').cuda().eval()
    order=sorted(range(len(data)),key=lambda i:len(ids[i]))
    with torch.inference_mode(),out.open('x') as f:
        for j in range(0,len(order),16):
            group=order[j:j+16];length=max(len(ids[i]) for i in group)
            batch=torch.tensor([[2]*(length-len(ids[i]))+ids[i] for i in group],device='cuda');mask=torch.tensor([[0]*(length-len(ids[i]))+[1]*len(ids[i]) for i in group],device='cuda')
            positions=(mask.cumsum(1)-1).clamp(min=0)
            logits=model(batch,attention_mask=mask,position_ids=positions,logits_to_keep=1,use_cache=False).logits[:,-1].float()
            scores=logits.log_softmax(-1)[:,[x[0] for x in choice]].cpu().tolist()
            for i,s in zip(group,scores):emit(f,i,s)
else:
    local=Path(os.environ['EVAL_LOCAL']);lib=local/'ollama/lib/ollama'
    exe=local/'score_47k_windows'
    assert hashlib.sha256(exe.read_bytes()).hexdigest()==json.loads((r/'scorer_probe.json').read_text())['scorer_sha256']
    env=dict(os.environ,LD_LIBRARY_PATH=str(lib/'cuda_v12')+':'+str(lib),OMP_NUM_THREADS='6')
    cmd=[str(exe),str(snap/('dense-3b-47k-'+fmt+'.gguf')),str(lib/'libggml-cpu-haswell.so'),str(lib/'cuda_v12/libggml-cuda.so'),'4096']
    with (d/fmt/'boolq-scorer.log').open('w') as log,subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,env=env) as proc,out.open('x') as f:
        try:
            for i,seq in enumerate(ids):
                scores=[]
                for opt in choice:
                    proc.stdin.write(' '.join(map(str,[len(seq)-1,*seq,*opt]))+'\n');proc.stdin.flush()
                    n,nll=proc.stdout.readline().split();assert int(n)==1;scores.append(-float(nll))
                emit(f,i,scores)
        finally:
            proc.stdin.close()
            assert proc.wait(timeout=60)==0
print(fmt,'BoolQ complete')
