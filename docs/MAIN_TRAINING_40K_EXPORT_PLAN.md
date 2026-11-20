# 40,000스텝 목표 변경 및 추론 패키징 계획

> 2026-11-08 최신 사용자 요청으로 [35,000스텝 완료·패키징 계획](MAIN_TRAINING_35K_EXPORT_PLAN.md)이 본 계획을 대체한다. 이 문서는 과거40k 준비·검증 이력이며 실행 기준이 아니다.

2026-11-08 사용자 요청을 반영한 목표 개정이다. **목표는40,000으로 변경하되 현재 실행 계약은57,000이며 아직 전환하지 않았다.** 아래 계획은 재개·변환·런타임 검증 통과 증거가 아니다. 과거57,000 계획/인증은 원본 증거로 보존한다.

## 목표와 일정

| 항목 | 기존 실행 | 변경 목표 |
|---|---|---|
| 절대 committed step | 57,000 | 40,000(기존 진행분 포함) |
| 토큰/update | 2,097,152 | 동일 |
| 총 학습 토큰 | 119,537,664,000 | 83,886,080,000 |
| LR warmup/peak | 1,000 / 3e-4 | 동일 |
| 마지막15% 선형 감쇠 | 48,450부터 | 34,000부터40,000까지 |
| 최소 LR 비율 | 0.1 | 동일(마지막 update는0부터 세는 구현의 경계값 검증) |
| 경량 평가/체크포인트 | 250/500step | 유지, 최종40,000 필수 |
| 정상 보존 | NFS최신3 + 로컬과거최대15 | 유지, 이전run 경로의 소유권 별도 |
| 최종 산출물 | native 전체상태/평가/복원/보고 | 기존 조건 + 실제 추론 패키지 및 런타임 테스트 |

17,000step/35,651,584,000tokens(기존 총량의29.82%)를 줄인다. 12:04 UTC 관측6980step·136,646.9956tokens/s 기준 순수 연산 잔여 약140.77시간, 기존 대비 약72.47시간 절약 추정이다. 평가·저장·전환·복원·패키징 시간은 별도이며 품질 동등성을 보장하지 않는다. 이 값은 예산 산정용 관측값이며 현재 진척으로 재사용하지 않는다.

## M1 — 변경 계약과 전환기 구현·검증

현재 `train/runtime.py`의 재개 계약은max_steps/LR/코드 해시를 포함한다. `main.json`만 수정하면 재개 검증이 실패하고, 이미 실행 중인 프로세스에는 적용되지 않는다. `stop_after=40000`만 설정하면 기존48,450 감쇠 시작 전 종료하므로 채택하지 않는다.

- 후보 새 run: `runs/3b_full_40k_v1`. 생성 직전 존재 여부·소유자·실행 PID를 다시 확인한다. 원본run/체크포인트/입력의 meta를 덮어쓰지 않는다.
- 부모의 인증된 완전 재개 체크포인트와 계약·코드·입력 SHA·8rank ledger를 고정한다. 별도 보호 사본과 소유권/보존 예산을 확정한 뒤 이전run의 자동 정리로 부모 증거가 사라지지 않게 한다.
- 허용 차이는 목표step/LR감쇠 일정 및 새 출력·관리 경로와 검증된 전환 구현으로 제한한다. 모델/optimizer/8rank RNG/샘플러·절대step/기존 토큰 투입 이력을 보존한다. 일반 재개 검증은 느슨하게 바꾸지 않는다. 명시적 부모→자식 lineage 증명을 추가한다.
- 작은 fixture로 허용 변경/금지 변경/손상·중단·재실행을 확인한다. 실제 부모 전체상태를 새 계약으로 읽고 별도 진단 경로에서8GPU 복원 및 다음 update를 검증한다. 34,000 이전 LR 동일성·optimizer/RNG·입력 순서·step 연속성을 확인하고 진단 update는 본학습 committed량에 합산하지 않는다.
- 현재 진행분이34,000 이전일 때만 기존 구간 LR 동일성 설명이 성립한다. 적용이 늦어지면 새 증거와 일정 재검토가 필요하다.
- 정상 체크포인트 경계에서 감독기와 worker의 중단·최종 인증을 조정한다. 프로세스 생존/소유자를 확인하고 두 학습기가 같은GPU/출력에 동시에 쓰지 않게 한다. 관측 timeout만으로 재시작하지 않는다. 전환 실패는 증거를 보존하고 사용자에게 보고한다.

변경 대상을 단순 문자열 치환하지 않고 새 계약의 target_step에서 일정을 유도한다:

| 대상 | 필요한 변경 |
|---|---|
| `train/runtime.py`, `train/monitored.py`, `train/parallel_contract.py` | 명시적 migration/lineage, 기존 계약 검사 유지, ledger 연속성 |
| `scripts/supervise_main_training.py` | 종료·최종cp·고정 생성·재개 검증의57k 가정 제거 |
| `scripts/audit_main_training.py`, `scripts/audit_checkpoint_retention.py` | 부모/자식 committed prefix 및40k 최종 검증 |
| `scripts/evaluate_final_main.py` | 사전준비39,500/최종40,000 경로·입력 계약 |
| `scripts/run_final_resume_probe.py`, `train/final_resume_probe.py`, 관련 verifier | 실제40k 전체상태를 별도경로에서 복원; 본학습 목표를 늘리지 않음 |
| `scripts/run_final_retention_audit.py` | 새run 보존 소유권/최종40k 인증 |
| 운영 observer/report helpers·완료조건 JSON | 목표/ETA/진척·알림·최종 경로, 과거 증거 불변 |

현재 로딩된 코드/계약을 작업 중 바꾸지 않도록 별도 버전 경로에서 개발·검증한다. 기존 observer/최종평가/복원/보존 대기 프로세스도 전환 시 새 계약으로 교체하고 PID·시작시각·인수·해시를 기록한다. 고정 생성은 기존250/2500/5000 증거를 이어받고10,000부터5,000 간격 및40,000 최종까지, 겹친 최종 일정은1회 수행한다. 서버30초/대화1시간+500step 정책을 유지한다.

### M1 구현 진척 (2026-11-08)

13:15 UTC 추가: PID-fd 인계제어11CPU검사, 계보발행/worker연결19CPU검사 및 실제7000 원본ledger 재집계 완료. `seal_horizon_handoff.py`는 live 또는 같은run 새PID·stale원본·보호사본변경·외부잔여물을 거부한다. 실제저장경계orchestration/3B보호복사/8GPU진단/40k최종controller/운영전환은 미완료. live준비본과bundle_v1을실행증명으로사용하지않으며최종인계때재생성한다. 상세근거 STATUS 및 `runs/main_training_40k_plan_v1/handoff_seal_validation.json`.

`train/horizon_transition.py`와 `train/parent_checkpoint_resume.py`의 독립 검증/외부부모 선택·DCP로드 guard 구현 완료.13개CPU테스트와 실제7000 부모 읽기전용 검토 통과(`runs/main_training_40k_plan_v1/parent_resume_validation.json`). 실제8GPU복원·운영worker/감독기 연결·부모보호·계보연결·실행전환은 미완료이며 이 결과로 대체하지 않는다.

## M2 — 학습 중 CPU에서 추론 변환 준비

REF-EXPORT/X01과 REF-EVAL을 적용한다. 현재repo에는 HF/GGUF 변환기가 없으며 실제 변환은 미검증이다.

- native→HF `LlamaForCausalLM` 후보: vocab65536, hidden3072, layer28, head24/KV8, intermediate8192, max_position4096, rope_theta500000, RMS eps1e-5, attention bias없음, **tie_word_embeddings=false**. native `out.weight`를 별도lm_head로 보존한다.
- Q/K/V/O·SwiGLU·norm·embedding·head의 키/shape/dtype 전체 대응 및 누락/추가/손상 거부. split-half RoPE 등 실제 연산 의미도 검사한다.
- 작은 tied/untied FP32 모델의 변환 전후 logits 기준 atol1e-5/rtol1e-4. BF16은 사전 기준을 별도로 정하고 실제3B teacher-forced logits/NLL·고정 생성과 비교한다. 다른 커널의 greedy 차이는 수치 오차와 함께 분석한다.
- 최종 tokenizer.json을 그대로 포함한 HF fast tokenizer/config/special IDs를 작성한다. 현재 Digits(individual_digits=true)+ByteLevel 전처리, BOS0/EOS1/PAD2와 자동 special 삽입 여부를 보존한다. 한/영/숫자/수학/코드/공백/Unicode의 token IDs와 decode를 원본과 대조한다.
- GGUF 변환기의 지원 pre-tokenizer와 실제 일치하는지 조기 확인한다. 모르는 전처리를 GPT2/Llama3 이름으로 바꿔 통과시키지 않는다. 지원이 없으면 정확한 구현과 Ollama 포함 런타임 검증이 필요하다. 미지원은 미완료로 기록한다.
- 기존 torch 학습 환경을 업그레이드하지 않고 독립 환경/컨테이너와 고정 패키지·llama.cpp commit을 사용한다. 현재 vLLM/Ollama/llama-cli 미설치다. CPU 준비만 병행하며 GPU 검증은 학습GPU 해제 후 수행한다.

## M3 — 40,000 최종 본학습 검증

40,000×8=320,000 committed rank/update 쌍과83,886,080,000tokens, 소스별 실제 투입량·부모/자식 중복 및 재실행량을 확인한다. 최종 NFS model/optimizer/8rank RNG/meta/계약/ledger·전체 SHA와 인증, 별도 실제8GPU 복원·재개, native 한/영 PPL·6소스평가·고정 생성, NFS/로컬 보존·잔여물을 검증한다. 이전57k용 완료조건 JSON을 수정해 과거증거로 오인시키지 않고 새40k 버전을 만든다.

현재5000 생성의 반복/산술 오답/코드 문제는 미해결이다. loss 감소·파일 로딩 성공·추론API 응답을 실용 품질 성공으로 표현하지 않는다. 40k 단축의 품질 영향은 최종 측정으로 보고한다.

## M4 — vLLM 실행 패키지(우선)

후보 `artifacts/3b_base_40k_v1/hf/`: sharded safetensors, config.json, generation_config.json, tokenizer.json/tokenizer_config.json/special token 정보, SHA manifest, 원본cp·변환기·환경 provenance, 모델 카드·실행 설명서. 재개용 optimizer/RNG는 추론 패키지에서 제외하지만 원본 NFS 완전상태는 보존한다.

독립환경에서 로컬 model 경로로 `vllm serve`를 실행하고 localhost completion API의 한/영/수학/코드 요청·streaming·EOS/길이제한·context4096·오류응답을 검증한다. 단일H200에서 시작해 latency/처리량/VRAM을 측정하고 추가 병렬화는 필요할 때 비교한다. 모든 실행 명령·runtime 버전·실제 출력과 native/HF 대비 차이를 보고서에 남긴다. 공식 지원목록의 LlamaForCausalLM 존재는 이 사용자 모델의 통과 증거가 아니다.

## M5 — Ollama용 패키지 및 사용 테스트

토크나이저 호환 검증 후 HF→GGUF F16/BF16→Q8_0/Q4_K_M 후보를 생성한다. 각 양자화본의 크기·메모리·속도·NLL/고정 생성 변화로 선택하며 예상 파일 크기를 실측처럼 기록하지 않는다. `Modelfile`, `ollama create`, `ollama run` 및 로컬API smoke test의 재현 명령과 결과를 포함한다. 우선 native와 같은 raw completion을 사용하며 학습되지 않은 채팅 template를 임의 추가하지 않는다.

사용자는 vLLM **또는** Ollama에서 테스트 가능한 모델을 요청했다. vLLM 성공을 우선 필수 런타임 gate로 삼고 Ollama를 추가 목표로 준비한다. Ollama 비호환이 확인되면 이유/대안을 명시하고 통과했다고 표시하지 않는다. 둘 다 보장한다고 보고하려면 각각 실제 검증이 필요하다. SFT/공식 벤치마크/외부 모델 업로드는 자동 수행하지 않는다.

## 공간·완료 기준

- full checkpoint 약36.011GiB. 보호 부모사본1개 및 전환 중 자식 작성/검증 사본·기존 진행 작업을 중복 없이 peak 계산한다. 이전run 보존분을 자동 삭제 대상으로 편입하지 않는다.
- BF16 weights 약6.44GB, FP32 약12.9GB는 parameter 기준 추정이며 실제shard·GGUF·양자화본·runtime 이미지/캐시·tmp를 별도 합산한다. 준비 시 추론 작업용100GiB 후보 예산을 실측으로 확정하고 초과 전에 중단/보고한다.
- 로컬 임시1,024GiB 및 저장소별200GiB 안전 여유, 현재3+15 보존의 향후peak를 먼저 확보한다. `/`와`/tmp` 동일FS는 합산하지 않는다. 외부 업로드·추가 비용 없음.
- 완료: 목표40k·최종 저장/실제복원/native평가/보존 검증 + HF 수치·tokenizer 검증 + vLLM 실제 추론 + Ollama 호환 결과·가능 시 실제 테스트 + 재현 패키지/완료보고. 런타임 동작과 모델 품질 한계를 분리한다.
- 현재 완료: 코드/계약/아키텍처/토크나이저/공식 지원 경로 조사와 계획 기록. 미완료: migration 구현·실제적용, exporter·런타임 설치/테스트, 모든40k 최종검증.
- 권장: 계약 전환/최종검토 Astra/High, 독립 변환기 구현 Sol/High, 정상감시 Luna/Low. 주세션 자동 변경·서브에이전트 사용 없음.

공식 참고: [vLLM 지원 모델](https://docs.vllm.ai/en/latest/models/supported_models/), [Ollama 모델 가져오기](https://docs.ollama.com/import), [llama.cpp 변환기](https://github.com/ggml-org/llama.cpp/blob/master/convert_hf_to_gguf.py). 실제 실행에는 검증한 버전을 고정한다.
