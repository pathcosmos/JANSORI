import ast,collections,hashlib,json,os,re,subprocess,sys,tempfile,unicodedata
from pathlib import Path
from tokenizers import Tokenizer
import numpy as np
d=Path(__file__).resolve().parent;r=d.parent/'3b_47k_hub_release_v1'
snapshot=json.loads((r/'jansori-hub-v1/public-snapshot.json').read_text())
t=Tokenizer.from_file(str(Path(snapshot['local_path'])/'tokenizer.json'))
items=json.loads((d/'quality-inputs.json').read_text())
def normalize(s):
    s=unicodedata.normalize('NFKC',s).strip().casefold()
    s=re.sub(r'^[*#\"“‘\s]+','',s);s=re.split('[.!?。;\\n]',s)[0]
    s=re.sub(r'(입니다|이다|이에요|예요)$','',s.strip().strip('.,:!?"”’*'))
    s=re.sub(r'^(the|a|an)\s+','',s)
    return re.sub(r'[\s\-]','',s)
colors={'파랑':['파란색','파란','청색','파란색 상자'],'초록':['초록색','녹색'],'보라':['보라색'],'노랑':['노란색','황색'],'빨강':['빨간색','빨간','적색'],'주황':['주황색'],'분홍':['분홍색'],'갈색':['갈색']}
def code_result(item,text):
    lines=text.splitlines(keepends=True)
    body=''
    for l in lines:
        if l.strip() and not l.startswith((' ','\t','#')):break
        body+=l
    src=item['prompt']+body
    try:tree=ast.parse(src)
    except SyntaxError:return dict(parse=False,body=False,passed=0,all_pass=False)
    has=len(tree.body[0].body)>1
    if not has:return dict(parse=True,body=False,passed=0,all_pass=False)
    with tempfile.TemporaryDirectory() as tmp:
        p=subprocess.run([sys.executable,'-I','-S',str(d/'code_case.py')],input=json.dumps([item['name'],src,item['tests']]),text=True,cwd=tmp,env={},stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=4)
    result=json.loads(p.stdout) if p.returncode==0 else {'passed':0,'failed_process':True}
    return dict(parse=True,body=True,all_pass=result['passed']==len(item['tests']),**result)
def math_ok(text,answer):
    line=unicodedata.normalize('NFKC',text.strip().splitlines()[0] if text.strip() else '').strip()
    m=re.match(r'^([+−-]?\d[\d,]*)(?:\s*(?:개|입니다|이다))?(?=$|[.!。]|\s*$)',line)
    return bool(m and int(m[1].replace(',','').replace('−','-'))==answer)
def grade(fmt):
    result=[]
    rows={x['id']:x for x in [json.loads(l) for l in (d/fmt/'quality.jsonl').read_text().splitlines()]}
    assert len(rows)==len(items)
    for q in items:
        x=rows[q['id']];ids=t.encode(x['text']).ids; grams=list(zip(ids,ids[1:],ids[2:],ids[3:]))
        g=dict(id=q['id'],tag=q['tag'],lang=q['lang'],band=q.get('length_band',q.get('level')),tokens=x['n'],limit=x['limit'],early=x['n']<x['limit'],reason=x['reason'],grams=len(grams),repeated=len(grams)-len(set(grams)))
        if q['tag'] in ('fact','reading'):
            a=normalize(x['text']); targets=set(q['answers'])
            for c in q['answers']:targets.update(colors.get(c,[]))
            g['ok']=a in {normalize(s) for s in targets}
        if q['tag']=='math':g['ok']=math_ok(x['text'],q['answer'])
        if q['tag']=='code':g.update(code_result(q,x['text']));g['ok']=g['all_pass']
        if q['tag']=='regression':
            name='vllm-3' if fmt=='BF16' else 'ollama-1'
            old=json.loads((r/'jansori-hub-v1'/name/(fmt+'-generation.json')).read_text())['cases']
            ref=next(o for o in old if o['prompt']==q['prompt'])
            g['ok']=(x['text'],x['n'])==(ref['text'],ref['tokens'])
        result.append(g)
    (d/fmt/'grades.json').write_text(json.dumps(result,ensure_ascii=False,indent=1))
def bootstrap():
    fmts=['BF16','Q8_0','Q4_K_M']
    allrows=[]
    for folder in ('eval-bf16-v1','eval-q8-v1','eval-q4-v1'):
        p=r/folder
        h=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
        res=json.loads((p/'result.json').read_text());assert h(p/'result.json')==json.loads((p/'.done').read_text())['result_sha256'] and h(p/'documents.jsonl')==res['documents_sha256']
        allrows.append([json.loads(x) for x in (p/'documents.jsonl').read_text().splitlines()])
    assert all([(a['text_sha256'],a['valid_tokens'],a['language']) for a in v]==[(a['text_sha256'],a['valid_tokens'],a['language']) for a in allrows[0]] for v in allrows)
    out={};rng=np.random.default_rng(47000)
    for lang in ('ko','en'):
        vs=[[a for a in v if a['language']==lang] for v in allrows]
        w=np.array([a['valid_tokens'] for a in vs[0]]);nll=np.array([[a['nll_sum'] for a in v] for v in vs])
        idx=rng.integers(0,len(w),size=(2000,len(w)))
        sampled=np.exp(nll[:,idx].sum(2)/w[idx].sum(1))
        ppl=np.exp(nll.sum(1)/w.sum())
        out[lang]={fmt:dict(ndocs=len(w),tokens=int(w.sum()),ppl=ppl[i],ppl_ci=np.quantile(sampled[i],[.025,.975]).tolist(),change_pct=(ppl[i]/ppl[0]-1)*100,change_ci_pct=np.quantile((sampled[i]/sampled[0]-1)*100,[.025,.975]).tolist()) for i,fmt in enumerate(fmts)}
    (d/'ppl-bootstrap.json').write_text(json.dumps(out,indent=1))
if __name__=='__main__':
    bootstrap()
    for fmt in sys.argv[1:]:grade(fmt)
