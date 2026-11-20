"""Create a non-release model card/use draft from current frozen contracts.

No model loading, server launch, publication, license assignment or final pass.
The final card must be filled from actual35k evidence and a quality review.
"""
import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
import shlex
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve,artifact_path,sha256_file,atomic_json


def prepare(output):
    from scripts.run_final_serving import load_contract
    from inference.vllm_server import server_arguments
    from inference.hf_export import layout
    from model.model import get_config
    from tokenizers import Tokenizer
    from inference.runtime_package import exact_path
    output=exact_path(resolve(output))
    if not output.is_relative_to(resolve('runs/main_training_35k_plan_v1')) or output.exists():
        raise ValueError('Use a new draft namespace within the35k preparation root')
    serving=resolve('artifacts/3b_base_35k_v1/vllm_serving_v1');c=load_contract(serving)
    main=resolve(c['main_run']);cfg=json.loads((main/'main.json').read_text())
    mix=json.loads(resolve(cfg['mix']).read_text())
    model=get_config(cfg['model'],vocab_size=cfg['vocab_size'],max_seq_len=cfg['seq_len'])
    weights=layout(model);params=sum(math.prod(shape) for _,shape in weights.values())
    if model.tie_embeddings or params!=3221400576 or cfg['max_steps']!=35000:
        raise ValueError('Unexpected final model architecture/target')
    target=cfg['max_steps']*cfg['global_tokens']
    if target!=73400320000 or mix['tokenizer_sha256']!=c['tokenizer_sha256']:
        raise ValueError('Unexpected data/target contract')
    source_rows=[]
    for name,s in mix['sources'].items():
        source_rows.append(dict(source=name,weight=s['weight'],available_train_tokens=s['tokens'],
            expected_draw_tokens_at_target=s['weight']*target,
            expected_exposures_at_target=s['weight']*target/s['tokens'],
            actual_committed_source_tokens=None,source_contract_id=s['contract_id'],
            legacy_expected_exposures_in_mix=s.get('expected_exposures')))
    if not math.isclose(sum(s['weight'] for s in source_rows),1,abs_tol=1e-12):
        raise ValueError('Mix weights do not sum to one')
    raw=Tokenizer.from_file(str(resolve(c['tokenizer'])))
    examples=[dict(id='ko',prompt='한국어 언어 모델은'),dict(id='en',prompt='A language model is'),
              dict(id='math',prompt='12 + 7 ='),dict(id='code',prompt='def add(a, b):\n')]
    request=dict(model=c['model_alias'],prompt=examples[0]['prompt'],max_tokens=64,temperature=0,
                 add_special_tokens=False,ignore_eos=False)
    stream={**request,'stream':True,'stream_options':dict(include_usage=True)}
    argv=server_arguments(c)
    argv[argv.index('--port')+1]='8000'
    python=str(Path(c['venv'])/'bin/python')
    command=[python,'-m','vllm.entrypoints.openai.api_server',*argv]
    env=dict(CUDA_VISIBLE_DEVICES='0',OMP_NUM_THREADS='4',MKL_NUM_THREADS='4',OPENBLAS_NUM_THREADS='4',
        TOKENIZERS_PARALLELISM='false',RAYON_NUM_THREADS='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
        VLLM_NO_USAGE_STATS='1',DO_NOT_TRACK='1',VLLM_USE_V1='1',VLLM_WORKER_MULTIPROC_METHOD='spawn')
    shell=shlex.join(['env',*[f'{k}={v}' for k,v in env.items()],'taskset','-c','8-15',*command])
    lock=resolve('runs/main_training_35k_plan_v1/serving_environment_v2/requirements.lock')
    source_paths=['scripts/prepare_model_usage.py','model/model.py','inference/hf_export.py',
        'inference/vllm_server.py','train/data.py',artifact_path(main/'main.json'),artifact_path(main/'contract.json'),
        artifact_path(serving/'contract.json'),cfg['mix'],c['tokenizer'],artifact_path(lock),
        'runs/main_training_readiness_v1/quality_review.json','runs/main_training_35k_plan_v1/ollama_compatibility_v2/reviewed.json']
    inputs={n:sha256_file(resolve(n)) for n in source_paths}
    table='\n'.join(f"| {s['source']} | {s['weight']*100:.6f}% | {s['available_train_tokens']:,} | {s['expected_draw_tokens_at_target']/1e9:.6f}B | {s['expected_exposures_at_target']:.6f} | 미확정 |" for s in source_rows)
    card=f'''# llm-dense-3b-35k — 모델 카드 초안

**배포 전 초안. 학습·최종 검증은 진행 중이다. 이 문서는 사용 가능 판정이나 완료 인증이 아니다.**

한국어 중심의 Dense 자기회귀 Base completion 모델이다. 직접 구현한 PyTorch decoder를 학습하고 HF Llama 호환 구조로 변환한다. Llama 계열 구조를 사용한다는 사실이 다른 모델의 사전학습 가중치를 사용했다는 뜻은 아니다. SFT/대화 튜닝을 수행한 모델로 표시하지 않는다.

| 항목 | 고정 설정 |
|---|---|
| 파라미터 | {params:,}; 모델 가중치 {len(weights)} tensor의 shape 합산, 최종 파일 대조는 별도 |
| 구조 | {model.n_layers}층 / hidden {model.dim} / FFN {model.ffn_dim} / attention heads {model.n_heads} / KV heads {model.n_kv_heads} |
| 연산 | RMSNorm·RoPE(theta={model.rope_theta:g})·GQA·SwiGLU, embedding/head 비공유 |
| 어휘·문맥 | 원본 raw65536 tokenizer / 최대4096토큰 |
| 특수토큰 | BOS0 / EOS1 / PAD2; chat template 미부여 |
| 학습 장치·정밀도 | H200×8, deterministic eager·bf16·FSDP2 |
| 목표 | 누적35,000 update·{target:,}tokens; 실제 완료량은 최종 감사 전 미확정 |
| 배치 | seq4096 / micro4 / accumulation16 / global2,097,152tokens |
| LR | peak3e-4, warmup1000, decay29750→35000, 최저비율0.1 |
| 평가·저장 | 평가250 / 저장500스텝; 일반평가와 공식벤치마크 구별 |

원본 검증된8,000스텝 prefix를 새run에 이어 누적35,000을 목표로 한다. 진단·재실행 토큰을 목표 달성량에 더하지 않는다. 토크나이저는 train-only 약3GB 원문에서 학습했고 SHA256은 `{c['tokenizer_sha256']}`이다.

## 데이터와 투입량

설계 혼합은 한국어/영어/코드/수학50/30/10/10이다. 아래 예상량은 확률 가중치×목표 토큰의 계산값이다. 실제 소스별 투입량은8rank ledger의 부모/자식 중복 없는 committed 합계를 확인한 뒤 기입한다. `mix.json`의 과거 `expected_exposures` 값을 현재 결과로 사용하지 않는다. sampler는 `weight`를 사용한다.

| 소스 | 가중치 | 사용 가능 train 토큰 | 목표 기준 예상 추출량 | 예상 노출 횟수 | 실제 committed |
|---|---:|---:|---:|---:|---|
{table}

정제/중복제거·분할·토큰화 및 소스 계약은 `inputs.json`의mix/토크나이저/코드해시와 원 프로젝트의 최종데이터 증명을 따른다. 이 초안에서 데이터별 권리나 모델 배포 라이선스를 새로 부여하지 않는다. 모델 라이선스는 현재 지정하지 않았다. 외부 업로드는 수행하지 않는다.

## 평가와 한계

최종3B native 한국어/영어 PPL,6소스 평가 및 고정 생성, 실제8GPU복원,HF FP32 동일성,vLLM bf16 엔진/API 결과는 **최종 실제 증거 대기**다. 추론 스모크 통과를 품질 벤치마크 통과로 해석하지 않는다. native PPL은 고정1,466문서의663,905한국어·705,634영어 targets 범위이며 전체holdout/공식벤치마크를 대표한다고 주장하지 않는다.

1B 리허설에서 반복 생성·산술 오답·코드 오류,64토큰까지EOS 미도달이 관측됐다. 영어/수학 중간평가 변동의 원인도 확정되지 않았다. 이 결과를 최종3B의 실제 오답 또는 해결 증거로 대체하지 않는다. 최종 생성의 반복·정답·기능 오류를 별도로 검토해 남은 제한을 갱신한다. loss 감소는 실용 품질 성공과 별개다.

현재 확인한 Ollama/GGUF 경로에서는 원본 Digits+ByteLevel 토크나이저 지원이 확인되지 않았다. 고정llama.cpp 인식 함수는 거부했고 Ollama v0.40.15 소스의 Digits 미지원 분기를 검토했다. 전체Ollama 실행 실패를 측정한 것은 아니다. 강제 tokenizer치환·지원 이름 변경 없이 HF/vLLM 검증 경로를 사용한다.

## 파일과 복구

HF 가중치는 `artifacts/3b_base_35k_v1/hf/`에 FP32 safetensors로 발행할 예정이다. vLLM 로딩은 bf16이며 원FP32 수치 동등성 검사와 실행 검사를 구분한다. 양자화는 완료하지 않았다. HF 패키지는 optimizer/RNG를 포함한 학습 재개용 체크포인트가 아니다.

학습 재개 기준은 NFS의 인증된 `runs/3b_full_35k_v1/training/ckpt_0035000`과 계약/계보/ledger다. NFS 최신3개+로컬 과거최대15개 순환 정책에서 보호 부모/진단 증거는 별도다. 최종 payload/SHA·복원·보존 결과를 완료 보고서에 연결한 뒤 이 초안을 확정한다.
'''
    usage=f'''# 모델 사용 안내 초안

**아직 실행하지 않는다.** 이 안내는 최종35k/HF/API 검증 완료 후 사용할 후보 명령이다. 현재 학습 GPU를 공유해 서버를 띄우지 않는다. 초안의 CLI 형식 확인과 실제최종3B 실행 성공은 다르다.

최종 사용 전 `artifacts/3b_base_35k_v1/vllm_serving_v1/report.json`과 해당 완료증명·원HF manifest·source 계약/해시를 검토한다. `.done` 하나만으로 판정하지 않는다. 자동 API 관리기는 검증 후 서버를 종료하며, 아래는 사용자가 별도로 기동하는 foreground 서버 명령이다. GPU0과127.0.0.1:8000이 비어 있어야 한다. 검증된 설정을 사용하지만 여러 서버를 중복 기동하지 않는다.

프로젝트 루트에서, 기존 격리 환경을 사용한다(학습 Python 환경 변경 없음):

```bash
{shell}
```

실행 시 컨텍스트는 입력+생성 합계4096이다. 이 Base 모델에는 chat template을 붙이지 않고 `/v1/completions`를 사용한다. 다른 터미널에서 이 초안 디렉터리의 JSON 파일을 이용한다.

```bash
curl --fail-with-body http://127.0.0.1:8000/v1/models
curl --fail-with-body -H 'Content-Type: application/json' --data-binary @completion.json http://127.0.0.1:8000/v1/completions
curl --no-buffer --fail-with-body -H 'Content-Type: application/json' --data-binary @streaming.json http://127.0.0.1:8000/v1/completions
```

기본 예제는 greedy·최대64토큰·EOS허용이다. 강제EOS/ignore_eos 진단을 사용자 기본값으로 사용하지 않는다. 응답은 생성 결과이며 정답/코드 정확성을 보장하지 않는다. 모델 문자열은 `{c['model_alias']}`이다. 서버는 localhost만 사용하고, 종료는 실행 터미널의Ctrl+C로 요청한 뒤 프로세스/GPU 해제를 확인한다. 외부바인딩·업로드·상시서비스 등록을 이 문서가 자동 실행하지 않는다.

격리환경이 유실되면 별도Python3.12 환경에 보존한 `requirements.lock`의171개 wheel/해시를 기준으로 재설치하고 CLI·tokenizer·실제GPU 검증을 반복한다. 이 초안은 패키지를 다운로드하거나 환경을 변경하지 않았다. 설치/전송비가 생기는 선택은 기존 확인 경계를 따른다. 현재 환경/OS/드라이버와 다른 기기에서 동작을 보장하지 않는다.

최종 사용패키지에는 이 문서·확정MODEL_CARD·원source/가중치/코드/환경SHA·실제생성 및native평가/API 지연·요청전체처리량·GPU관측peak를 함께 넣는다. 현재 그런 측정값을 초안으로 채워 넣지 않는다. 사용 문서는 HF의sealed 파일 목록에 추가하지 않고 별도use 디렉터리에 발행한다.
'''
    output.mkdir(parents=True)
    (output/'MODEL_CARD.draft.md').write_text(card)
    (output/'USAGE.draft.md').write_text(usage)
    (output/'requirements.lock').write_bytes(lock.read_bytes())
    atomic_json(output/'completion.json',request);atomic_json(output/'streaming.json',stream)
    atomic_json(output/'commands.json',dict(argv=command,environment=env,cpu_affinity=list(range(8,16)),
        model=artifact_path(resolve(c['package'])),port=8000,actual_command_executed=False,final_validation_required=True))
    atomic_json(output/'inputs.json',inputs)
    atomic_json(output/'data_exposure.json',dict(scope='expected_weighted_draws_not_committed_counts',target_tokens=target,sources=source_rows))
    atomic_json(output/'example_tokens.json',[dict(**e,ids=raw.encode(e['prompt'],add_special_tokens=False).ids) for e in examples])
    atomic_json(output/'report.json',dict(status='usage_and_model_card_draft_not_release',parameters_from_shapes=params,
        model_config=asdict(model),target_steps=35000,target_tokens=target,goal_complete=False,
        actual_final_model_verified=False,actual_serving_command_executed=False,
        license_assigned=False,source_inputs=inputs,files={p.name:sha256_file(p) for p in sorted(output.iterdir())},
        pending=['Actual final35k complete-state/restore/evaluation/retention','FinalHF numerical/SHA publication',
            'Actual offline/API model execution and measured latency/throughput/VRAM','Final quality review and evidence-bound user documents']))
    return output


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True)
    print(prepare(p.parse_args().output))
