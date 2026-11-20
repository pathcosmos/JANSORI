import collections,csv,hashlib,json,statistics
from pathlib import Path
import numpy as np
d=Path(__file__).resolve().parent
fmts=['BF16','Q8_0','Q4_K_M']
def read(p):return json.loads(p.read_text())
def rows(p):return list(map(json.loads,p.read_text().splitlines()))
def dump(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def med(x):return statistics.median(x)
def pct(a,b):return 100*a/b
def h(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def csvout(name,rs):
    with (d/name).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rs[0]));w.writeheader();w.writerows(rs)
qs={i['id']:i for i in read(d/'quality-inputs.json')}
blind=read(d/'story-blind.json')['ratings']; mapping=read(d/'story-key.json')
ppl=read(d/'ppl-bootstrap.json');perfs=[];qualities={};reg={};paired={};all_metrics={}
contract=read(d/'boolq-contract.json');labels=np.array([r['label'] for r in contract['items']])
seed=47000;rng=np.random.default_rng(seed);bidx=rng.integers(0,len(labels),(2000,len(labels)))
for fmt in fmts:
    folder=d/fmt;grade=read(folder/'grades.json')
    outputs={r['id']:r for r in rows(folder/'quality.jsonl')};assert set(outputs)==set(qs)
    assert all(outputs[k]['k']==len(q['ids']) for k,q in qs.items())
    def subset(tag,lang=None,band=None):return [r for r in grade if r['tag']==tag and (not lang or r['lang']==lang) and (not band or r['band']==band)]
    def count(tag,**kw):
        s=subset(tag,**kw);return [sum(x['ok'] for x in s),len(s)]
    qual={tag:{lang:count(tag,lang=lang) for lang in ['ko','en']} for tag in ('fact','reading','math')}
    qual['math_level']={band:count('math',band=band) for band in ['basic','two_step']}
    qual['reading_length']={band:count('reading',band=band) for band in ['short','long']}
    qual['regression']=count('regression')
    qual['early_stop_count']=sum(x['early'] for x in grade)
    cs=subset('code');sc=rows(folder/'scaffold.jsonl');assert len(sc)==len(cs)==12
    qual['code']={k:[sum(x['parse'] for x in vals),sum(x['body'] for x in vals),sum(x['all_pass'] for x in vals),sum(x['passed'] for x in vals)] for k,vals in [('plain',cs),('scaffold',sc)]}
    qual['stories']={}
    for lang in ('ko','en'):
        v=subset('story',lang=lang);scores=[blind[i][0] for i,s in mapping.items() if s['id'] in {x['id'] for x in v} and fmt in s['formats']]
        assert len(scores)==4
        qual['stories'][lang]=dict(rating_counts=[scores.count(i) for i in range(3)],rep4=pct(sum(r['repeated'] for r in v),sum(r['grams'] for r in v)),early=sum(r['early'] for r in v))
    bools=sorted(rows(folder/'boolq.jsonl'),key=lambda x:x['index'])
    assert [x['index'] for x in bools]==list(range(1404)) and np.all([b['label']==l for b,l in zip(bools,labels)])
    preds=np.array([b['pred'] for b in bools]);ok=(preds==labels);all_metrics[fmt]=ok
    f1=[2*np.sum((preds==j)&(labels==j))/(np.sum(preds==j)+np.sum(labels==j)) for j in (0,1)]
    qual['boolq']=dict(correct=int(ok.sum()),n=len(ok),pred_true=int(preds.sum()),macro_f1=float(np.mean(f1)),accuracy_ci_pct=np.quantile(ok[bidx].mean(axis=1)*100,[.025,.975]).tolist())
    dif=ok.astype(int)-all_metrics['BF16'].astype(int)
    qual['boolq'].update(flips=int(np.count_nonzero(dif)),gain=int((dif>0).sum()),loss=int((dif<0).sum()),difference_ci_pp=np.quantile(dif[bidx].mean(axis=1)*100,[.025,.975]).tolist())
    qualities[fmt]=qual
    groups=rows(folder/'perf.jsonl');assert len({p['key'] for p in groups})==len(groups)==45
    grouped=collections.defaultdict(list)
    for group in groups:grouped[group['length'],group['limit'],group['concurrency']].append(group)
    assert len(grouped)==9
    for (k,n,c),gs in sorted(grouped.items()):
        assert len(gs)==5
        calls=[v for g in gs for v in g['results']];ok=[v for v in calls if not v.get('error')];assert all(v['k']==k and v['n']==n for v in ok)
        good=[g for g in gs if all(not v.get('error') for v in g['results'])]
        walls=[max(row['end'] for row in g['results'])-min(row['start'] for row in g['results']) for g in good]
        lats=[v['latency'] for v in ok];first=[v['ttft']*1e3 for v in ok]
        p=dict(format=fmt,input_tokens=k,output_cap=n,clients=c,repeats=len(gs),requests=len(calls),success=len(ok),errors=len(calls)-len(ok),
               latency_med_s=med(lats),latency_min_s=min(lats),latency_max_s=max(lats),ttft_med_ms=med(first),ttft_min_ms=min(first),ttft_max_ms=max(first),
               decode_tok_s=med((v['n']-1)/(v['latency']-v['ttft']) for v in ok),tpot_med_ms=med((v['latency']-v['ttft'])*1000/(v['n']-1) for v in ok),
               batch_tok_s=med(c*n/w for w in walls),batch_req_s=med(c/w for w in walls),batch_wall_med_s=med(walls),
               vram_peak_mib=max(g['peak']['vram'] for g in gs)/2**20,pss_peak_mib=max(g['peak']['pss'] for g in gs)/2**20,cgroup_ram_peak_gib=max(g['peak']['ram'] for g in gs)/2**30,cgroup_file_peak_gib=max(g['peak']['file_cache'] for g in gs)/2**30)
        if fmt!='BF16':
            p.update(server_prefill_ms=med(v['server']['prompt_eval_duration']/1e6 for v in ok),server_decode_ms=med(v['server']['eval_duration']/1e6 for v in ok))
        else:p.update(server_prefill_ms=None,server_decode_ms=None)
        perfs.append(p)
csvout('PERFORMANCE.csv',perfs)
dump(d/'quality-summary.json',qualities)
v=rows(d/'dual.jsonl');assert len(v)==20
dual={}
for fmt in fmts[1:]:
    dual[fmt]={}
    for mode in ('seq','par'):
        g=[x for x in v if x['fmt']==fmt and x['mode']==mode];assert len(g)==5 and len({s['i'] for s in g})==5
        dual[fmt][mode]=dict(wall_med_s=med(s['wall'] for s in g),wall_range_s=[min(s['wall'] for s in g),max(s['wall'] for s in g)],overlap_med_s=med(s['overlap'] for s in g),combined_vram_mib=max(s['combined_vram'] for s in g)/2**20,pss_mib=max(s['pss'] for s in g)/2**20)
dump(d/'dual-summary.json',dual)
lines=['## H200에서 측정한 품질과 실행 설정','','이 결과는 47,000스텝의 [JANSORI 배포 커밋](https://huggingface.co/pathcosmos/JANSORI/tree/e68bfa3cb31e9589f4481a2e31d6d75c731fec9d)에 적용한다. BF16은 vLLM 0.11.2, Q8_0·Q4_K_M은 Ollama 0.40.16으로 같은 물리 H200 한 장에서 각각 실행했다. 이전 배포 검증의 6개 고정 입력은 형식별로 6/6 재현됐다.','',
'### 한국어·영어·수학·코드','','자체 생성 시험은 형식당 98개 입력이다. 짧은 사실 질문 24개, 색상 독해 16개, 산수 32개, 함수 12개, 이야기 이어쓰기 8개, 회귀 입력 6개를 사용했다. 세 형식의 입력과 생성 상한을 같게 했다. 사실·독해는 첫 답을 정규화해 허용 답과 비교했다. 산수는 요구한 정수가 첫 줄에 답으로 나왔는지 채점했다. 문항 수가 작으므로 한 문항 차이로 형식의 우열을 정하기 어렵다.','',
'| 형식 | 한국어·영어 사실 (각 12) | 한국어·영어 독해 (각 8) | 기본·두 단계 산수 (각 16) | 함수 전체 통과, 설명만 / 예시+반환문 (각 12) | BoolQ 정답 / 1,404 | BoolQ macro-F1 |','|---|---:|---:|---:|---:|---:|---:|']
for fmt,q in qualities.items():
    f=q['fact'];r=q['reading'];m=q['math_level'];c=q['code'];b=q['boolq']
    lines.append(f"| {fmt} | {f['ko'][0]} · {f['en'][0]} | {r['ko'][0]} · {r['en'][0]} | {m['basic'][0]} · {m['two_step'][0]} | {c['plain'][2]} / {c['scaffold'][2]} | {b['correct']} ({b['correct']/1404:.1%}) | {b['macro_f1']:.3f} |")
lines+=['','두 예시 함수와 대상 함수의 `return`까지 입력한 조건은 같은 12개 함수의 표현식 완성 문제다. 모델이 나머지 본문 전체를 작성하는 조건보다 쉽다. 출력의 들여쓰기를 수리하지 않고 첫 함수만 추출해 파싱하고, 함수당 4개 독립 사례의 값과 반환 타입을 검사했다. 세 형식 모두 가장 큰 포유류를 코끼리라고 답했다. 산수 총 정답은 모두 15/32였지만 정답을 낸 문항이 일부 달랐다.','',
'한국어 KoBEST BoolQ는 테스트 1,404개 전체에서 지문·질문 뒤 단일 토큰인 ‘ 거짓’·‘ 참’의 조건부 로그확률을 비교한 zero-shot 시험이다. BF16에는 같은 배포 가중치의 Transformers BF16 forward, GGUF에는 해당 파일의 llama.cpp 채점기를 사용했다. 다수 클래스는 참 705/1,404(50.2%)다. 다음 표의 참 예측 빈도와 macro-F1을 함께 해석한다. 데이터 라이선스는 CC BY-SA 4.0이며 시험의 학습 데이터 중복 여부는 미확인이다. [KoBEST 고정 revision](https://huggingface.co/datasets/skt/kobest_v1/tree/a5ea15e3ac77ed694b79f6204eb31889a2ba989f)','',
'| 형식 | BoolQ 참 예측 수 | BF16 대비 맞음→틀림 / 틀림→맞음 | 한국어 / 영어 이야기 반복 4-gram | 한국어 / 영어에서 장면을 전개한 출력 (각 4개) |','|---|---:|---:|---:|---:|']
for fmt,q in qualities.items():
    s=q['stories'];b=q['boolq'];lines.append(f"| {fmt} | {b['pred_true']} | {b['loss']} / {b['gain']} | {s['ko']['rep4']:.1f}% / {s['en']['rep4']:.1f}% | {s['ko']['rating_counts'][2]} / {s['en']['rating_counts'][2]} |")
lines+=['','반복률은 64토큰 생성물을 다시 토큰화해 처음 등장한 이후의 중복 4-gram 수를 전체 4-gram 수로 나눈 값이다. 이어쓰기 8개는 형식명을 가리고 같은 입력·같은 출력을 합쳐 단일 AI 판독자가 0–2점으로 분류했다. 표에는 서로 다른 세부 내용으로 장면이 전개된 2점 출력 수를 표시했다. 예를 들어 비가 그친 운동장이라는 영어 입력에서는 잔디 묘사가 같은 문장으로 반복됐다. 사람 평가자 간 일치도는 측정하지 않았다.','',
'### 언어별 holdout PPL','','검증된 1,466문서의 동일 토큰 ID·마스크·문서별 NLL 합을 재사용했다. 한국어 663,905개, 영어 705,634개 표적이다. 괄호 안은 BF16 대비 상대 상승률의 95% 구간이다. 문서를 언어별로 짝지어 2,000회 복원 추출하고 매번 표적 토큰 가중 평균 NLL로 PPL을 구했다.','',
'| 형식 | 한국어 PPL | 한국어 상승률 (95% 구간) | 영어 PPL | 영어 상승률 (95% 구간) |','|---|---:|---:|---:|---:|']
for fmt in fmts:
    l=['| '+fmt]
    for lang in ('ko','en'):
        s=ppl[lang][fmt];ci=s['change_ci_pct'];l.extend([f"{s['ppl']:.5f}",'기준' if fmt=='BF16' else f"{s['change_pct']:+.3f}% ({ci[0]:+.3f} ~ {ci[1]:+.3f}%)"])
    lines.append(' | '.join(l)+' |')
lines+=['','### 문맥 길이와 메모리','','서버를 예열한 뒤 입력 길이·출력 상한별로 5회씩 순서를 섞었다. 성능 입력은 한 단어당 한 토큰인 고정 어휘에서 seed로 뽑았으며 한국어 품질 시험 입력과 별도다. 각 형식에 같은 프롬프트를 전송했고 성공 응답의 입력·출력 토큰 수를 전수 확인했다. 아래는 출력 128토큰, 단일 요청의 중앙값이다. 첫 텍스트가 도착할 때까지가 TTFT이고 생성 속도는 이후 남은 토큰을 완료할 때까지의 속도다. 최고 VRAM은 요청 경계와 0.5초 간격에서 관측한 장치 전체 사용량이다.','',
'| 입력 토큰 | 형식 | 성공 / 시도 | TTFT (ms) | 전체 지연 (초) | 생성 토큰/초 | 최고 VRAM (MiB) |','|---:|---|---:|---:|---:|---:|---:|']
for k in (128,1024,3072,3968):
    for fmt in fmts:
        x=next(p for p in perfs if (p['format'],p['input_tokens'],p['output_cap'],p['clients'])==(fmt,k,128,1))
        lines.append(f"| {k:,} | {fmt} | {x['success']}/{x['requests']} | {x['ttft_med_ms']:.1f} | {x['latency_med_s']:.3f} | {x['decode_tok_s']:.1f} | {x['vram_peak_mib']:.1f} |")
lines+=['','Q4_K_M은 3,968+128 조건에서 한 요청이 반복 토큰 제한으로 종료됐으며 같은 입력의 별도 재현에서도 같은 오류가 발생했다. 이 행의 지연·속도는 성공한 4회로 계산했고 VRAM에는 실패 요청도 포함했다. 4,096은 입력과 출력을 합한 한도다. 서버가 예열된 상태에서 KV 캐시와 실행 버퍼를 재사용하므로 입력 길이가 바뀌어도 관측 최고 VRAM이 비슷할 수 있다.','',
'### 동시 요청과 형식 선택','','입력 1,024토큰·출력 32토큰에서 동시에 1·2·4개 요청을 보냈다. 표의 처리량은 요청 묶음 전체의 출력 토큰 수를 첫 요청 시작부터 마지막 완료까지의 시간으로 나눈 값의 중앙값이다. Ollama는 서버 병렬 슬롯 1개로 큐 대기를 포함한다. vLLM은 최대 2개 시퀀스를 함께 처리한다.','',
'| 형식 | 동시 1 / 2 / 4 전체 처리량 (토큰/초) | 동시 4 요청 지연 중앙값 / 관측 최댓값 (초) |','|---|---:|---:|']
for fmt in fmts:
    g=sorted([x for x in perfs if x['format']==fmt and x['input_tokens']==1024 and x['output_cap']==32],key=lambda v:v['clients'])
    lines.append(f"| {fmt} | "+' / '.join(f"{x['batch_tok_s']:.1f}" for x in g)+f" | {g[-1]['latency_med_s']:.3f} / {g[-1]['latency_max_s']:.3f} |")
lines+=['','형식별 권장 시작 설정은 다음과 같다. 속도 비교에는 가중치 정밀도뿐 아니라 런타임·커널·캐시 설정 차이도 포함된다.','',
'- **BF16 · 정확도 비교 기준, 짧은 첫 응답 또는 동시 요청.** H200 1장에 `--dtype bfloat16 --load-format safetensors --tensor-parallel-size 1 --max-model-len 4096 --max-num-seqs 2 --max-num-batched-tokens 4096 --gpu-memory-utilization 0.25 --num-gpu-blocks-override 512 --enforce-eager --no-enable-prefix-caching --generation-config vllm --seed 47000`을 사용했다. `VLLM_ATTENTION_BACKEND=FLASH_ATTN`과 `VLLM_USE_FLASHINFER_SAMPLER=0`을 적용했다. `/v1/completions`로 `temperature=0`과 목적에 맞는 `max_tokens`를 전달한다.','- **Q8_0 · Ollama의 품질 우선 기본값.** BF16에 가까운 PPL이 필요한 경우 `hf.co/pathcosmos/JANSORI:Q8_0`을 선택한다. `/api/generate`에서 `raw=true`, `stream=true`, `num_ctx=4096`, `num_predict=32`(짧은 답) 또는 `128`(이어쓰기), `temperature=0`, `repeat_penalty=1`, `seed=47000`을 사용한다. 이 H200 시험은 `num_gpu=99`, `num_thread=4`, `OLLAMA_NUM_PARALLEL=1`이었다.','- **Q4_K_M · 메모리 우선.** 같은 Ollama 옵션에 태그를 `Q4_K_M`으로 바꾼다. 긴 입력에서는 최대 길이보다 여유를 두고 생성 상한을 명시한다. 이번 3,072+128 시험은 5/5 완료됐다. Q4가 모든 입력 길이에서 가장 빠르다는 보장은 없다.','- **산수·코드.** 짧은 정답을 요구하는 단순 산수와 두 단계 산수 결과를 구분한다. 간단한 Python 함수 완성에는 함수 예시 두 개와 목표 함수의 `return` 접두어를 적용한 뒤 별도 테스트로 검증한다. 이 결과는 임의 프로그램 생성 능력을 평가하지 않는다.','']
(d/'MODEL_CARD_UPDATE.md').write_text('\n'.join(lines))
dual_lines=['### H200 두 장에서 요청을 병행한 경우','','GPU 0에 BF16, GPU 1에 Ollama 한 형식을 적재한 뒤 1,024토큰 입력과 32토큰 출력을 두 서버에 각각 요청했다. 형식 조합별로 서로 다른 입력 5개를 사용했다. 같은 입력을 순차와 병행에 모두 사용하고 어느 쪽을 먼저 측정할지는 교대했다. 각 측정 전 별도 1,024토큰 요청으로 직전 입력 캐시가 그대로 재사용되는 경우를 줄였다. 서버 시작·모델 로딩은 이 표에서 제외했다.','',
'| 동시 상주 형식 | 순차 / 병행 묶음 지연 중앙값 (초) | 병행 요청 중첩 중앙값 (초) | 두 출력 모두 동일한 입력 | 동일 출력 쌍만의 순차 / 병행 중앙값 (초) | 최고 동시 합산 VRAM, 순차 / 병행 (MiB) |',
'|---|---:|---:|---:|---:|---:|']
for fmt in fmts[1:]:
    gs={(a['mode'],a['i']):a for a in v if a['fmt']==fmt};same=[]
    for i in range(5):
        x,y=gs['seq',i],gs['par',i]
        if all(x[k]['text']==y[k]['text'] for k in ['bf16','quant']):same.append(i)
    a,b=dual[fmt]['seq'],dual[fmt]['par']
    dual[fmt]['same_output_indices']=same
    agree='미산출' if not same else f"{med(gs['seq',i]['wall'] for i in same):.3f} / {med(gs['par',i]['wall'] for i in same):.3f}"
    dual_lines.append(f"| BF16 + {fmt} | {a['wall_med_s']:.3f} / {b['wall_med_s']:.3f} | {b['overlap_med_s']:.3f} | {len(same)}/5 | {agree} | {a['combined_vram_mib']:.1f} / {b['combined_vram_mib']:.1f} |")
dual_lines+=['','양쪽 모델이 상주한 상태이므로 순차 요청도 두 모델의 VRAM을 유지한다. 동일 출력 쌍 수가 적을 때는 속도 차이를 동일 품질에서 보장되는 개선으로 해석하기 어렵다. 기존 공개 검증의 서버 시작부터 종료까지 잰 수치는 다른 지표다.','']
dump(d/'dual-summary.json',dual)
with (d/'MODEL_CARD_UPDATE.md').open('a') as f:f.write('\n'.join(dual_lines))
detail=['# JANSORI 상세 평가 결과','','[모델 카드 삽입용 본문](MODEL_CARD_UPDATE.md) · [문맥·동시성별 전체 CSV](PERFORMANCE.csv) · [품질 요약 JSON](quality-summary.json) · [공개 모델](https://huggingface.co/pathcosmos/JANSORI/tree/e68bfa3cb31e9589f4481a2e31d6d75c731fec9d)','',
'## 측정 범위','','같은 물리 H200 GPU 0을 한 형식씩 단독 사용했다. 3개 형식 × 98개 품질 요청, 별도 함수 프롬프트 3 × 12개, KoBEST 1,404개 × 3형식, 3 × 65개 단독 성능 요청을 수행했다. KoBEST와 함수 추가 시험은 단독 성능 시험 후 진행했다. 2GPU 추가 시험은 조합당 5종 입력에 순차·병행 각 1쌍이다. 양자화가 같더라도 서버·커널·KV 캐시 설정이 바뀌면 속도나 생성 결과가 달라질 수 있다.','',
'### 정답과 오류 유형','','| 형식 | 한국어 / 영어 산수 (각 16) | 짧은 / 긴 색상 독해 (각 8) | 설명만 코드 구문 분석 / 실행 본문 / 전체통과 (각 12) | 사례 통과, 설명만 / 예시+반환문 (각 48) | BoolQ 95% 정확도 구간 | BF16 대비 BoolQ 차이 95% 구간 (pp) |','|---|---:|---:|---:|---:|---:|---:|']
for fmt,q in qualities.items():
    b=q['boolq'];mc=q['math'];re=q['reading_length'];p,s=q['code']['plain'],q['code']['scaffold'];ci=b['accuracy_ci_pct'];dif=b['difference_ci_pp']
    detail.append(f"| {fmt} | {mc['ko'][0]} / {mc['en'][0]} | {re['short'][0]} / {re['long'][0]} | {p[0]} / {p[1]} / {p[2]} | {p[3]} / {s[3]} | {ci[0]:.2f}–{ci[1]:.2f}% | {dif[0]:+.2f} ~ {dif[1]:+.2f} |")
detail+=['','사실 질문 예시에서 세 형식은 대기 중 가장 많은 기체에 이산화탄소, 가장 큰 바다에 대서양이라고 답했다. 자체 문항의 정답 표기는 첫 답만 비교한다. 수학에서 동치 식을 적고 최종 정수를 쓰지 않은 출력도 오답이다. 색상 독해는 같은 설계의 인공 문맥이며 방해 문장 반복 여부가 길이 차이를 만든다. 자체 문항은 독립으로 작성했으며 학습 데이터 오염 여부를 전수 대조하지 않았다.','',
'코드에는 호출 전에 AST를 분석하고 첫 함수 본문만 보존했다. 빈 본문 대신 docstring만 있는 함수는 Python 구문 분석에 성공해도 실행 본문이나 단위 사례 점수를 얻지 못한다. 코드를 수정하거나 들여쓰기를 보충하지 않았다. 허용 AST와 내장 함수만 사용하고, 별도 작업 디렉터리·빈 환경·CPU/RAM 제한·파일 및 네트워크 시스템 호출을 차단하는 자식 프로세스에서 채점했다. 추가 프롬프트는 실행 결과를 바탕으로 확인한 탐색적 대조로, 모델에 두 예시와 반환문 접두어를 모두 제공했다. 48개 사례는 고정된 시험용 입력·기대값이다.','',
'KoBEST 선택지는 문장 뒤에서 각 한 토큰이므로 전체 로그확률과 토큰당 평균의 순위가 같다. 같은 문항을 2,000회 paired bootstrap한 정확도·차이 구간을 보고했다. 이 구간에는 prompt 선정이나 학습 오염의 불확실성이 포함되지 않는다. [KoBEST 원본 자료와 라이선스](https://huggingface.co/datasets/skt/kobest_v1/tree/a5ea15e3ac77ed694b79f6204eb31889a2ba989f)','',
'이어쓰기 표본의 단일 AI 블라인드 평가는 24개 응답을 입력·출력별로 합친 15개 텍스트에 수행했다. 0점은 새 서술 없음, 1점은 시작은 연결되지만 반복 지배, 2점은 서로 다른 세부 내용으로 전개다. 세 형식 모두 98개 기본 품질 요청에서 생성 상한 전에 멈춘 응답은 0개였다. [블라인드 판정 및 이유](story-blind.json) · [형식 연결표](story-key.json) · [문서별 paired PPL 구간](ppl-bootstrap.json)','',
'## 모든 단독 조건','','5회씩 교차 측정했다. 지연과 TTFT는 성공한 개별 요청 중앙값이다. TTFT는 첫 비어 있지 않은 텍스트 청크를 클라이언트가 읽은 시각 기준이다. gRPC·서버 내부의 첫 디코딩 시각과 다를 수 있다. 동시 요청일 때 큐 대기와 전송을 포함한다. 그룹 출력 처리량은 요청 묶음의 wall time 기준이다. 최대 VRAM·PSS는 각 0.5초 표본과 요청 그룹 직전·직후 값을 포함한 관측 최고치다. 5개 반복으로 불안정한 p95는 산출하지 않았다. CSV에는 TTFT 범위·요청 지연 범위·TPOT·RPS·컨테이너 RAM/파일 캐시·Ollama 서버 prefill/decode 시간도 있다.','',
'| 형식 | 입력 / 출력상한 / 동시 요청 | 성공 / 시도 | TTFT ms | 요청 지연 중앙값 (최소–최대) 초 | 묶음 출력 토큰/초 | 최고 VRAM / PSS MiB |','|---|---|---:|---:|---:|---:|---:|']
for x in perfs:
    detail.append(f"| {x['format']} | {x['input_tokens']} / {x['output_cap']} / {x['clients']} | {x['success']}/{x['requests']} | {x['ttft_med_ms']:.1f} | {x['latency_med_s']:.3f} ({x['latency_min_s']:.3f}–{x['latency_max_s']:.3f}) | {x['batch_tok_s']:.1f} | {x['vram_peak_mib']:.1f} / {x['pss_peak_mib']:.1f} |")
detail+=['','Q4 경계 조건에서 Ollama가 HTTP 200 이후 스트림 안에 오류를 보냈다. 최초 계측기가 이 오류 레코드를 해석하지 못해 종료했다. 기존 기록을 보존하고 동일 입력으로 오류를 재현한 뒤, 실패도 분모에 포함하도록 계측기를 수정해 완료한 그룹부터 재개했다. 5회 집계 중 이 입력은 한 번만 센다. 서비스 재기동 없이 재개 시 짧은 준비 요청을 한 번 추가했다. [스트림 재현](Q4_K_M/boundary-diagnostic.json)','',
'이 시험은 로컬에 저장된 배포 커밋을 재사용했으므로 신규 다운로드 시간은 재측정하지 않았다. 최초 다운로드·무결성과 기존 로딩 시간은 [배포 검증 기록](../3b_47k_hub_release_v1/jansori-hub-v1/RELEASE_REPORT.md)에 있다. 이번 표는 예열 후 요청만 대상으로 한다. 캐시된 모델 파일에서의 기동·모델 적재 및 초기 요청은 서버 로그와 형식별 calibration에 별도 보관했다. Q4 calibration 최종 파일은 재개 후 추가 준비 요청의 값이다. 기존 4,096토큰 전체 forward 검증은 [BF16 승인 근거](../3b_47k_hub_release_v1/bf16-accepted-v1/result.json)에 있다.','',*dual_lines,'',
'2GPU 첫 시도에서 순차와 병행의 생성 텍스트가 달라 중단됐다. 동일 입력·설정으로 전체 시험을 다시 수행하고 출력 일치 여부와 요청 중첩을 함께 기록했다. 첫 시도의 완료 전 데이터는 최종 표에서 제외했다. [최종 2GPU 원시 결과](dual.jsonl) · [최초 시도](dual-attempt1/dual.jsonl)','',
'## 재현에 사용한 근거','','[입력 및 정답](quality-inputs.json) · [성능 입력 생성 코드](inputs.py) · [단독 시험 실행](run.py) · [채점 기준](grade.py) · [후속 코드 조건](scaffold.py) · [KoBEST 채점](boolq.py) · [2GPU 시험](dual.py) · [서버 자원](hardware.json)','']
(d/'DETAILED_RESULTS.md').write_text('\n'.join(detail))
