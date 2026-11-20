# 35,000스텝 완료 및 추론 패키징 전환 계획

2026-11-08 사용자 최신 요청에 따라 **최종 목표를35,000스텝 + 추론 패키지·실제 실행 테스트로 개정**한다. 이전40k 계획을 대체한다. 기존 진행분을 포함한 절대step 목표이며35,000스텝을 추가 학습하는 뜻이 아니다. 현재 실행은 `runs/3b_full_v1`의57,000 계약이다. 이번 문서는 실행 설정 변경·GPU 재개·패키징 완료 증명이 아니다.

## 실행 승인 및 자동 전환 기동 (2026-11-08 15:04 UTC)

사용자의 **“전환해”**를 35k 실행 전환 승인으로 기록했다. `runs/main_training_35k_plan_v1/transition_authorization.json`과 `authorized_transition8000_plan.json`에 정확한 대상·코드/입력 SHA·구/신 보존 범위를 결합했다. 현재 승인된 활성 준비 묶음은 **bundle_v3**이며 이전 묶음은 근거 보존용이다.

- `runs/3b_35k_transition_v1`의 실제 PID934769/start_ticks142362953이 30초 주기로 **8,000스텝 인증·보존 정리 완료**를 기다린다. 현재7,670, 원57k 학습은 계속 진행하며 새35k run은 아직 미기동이다. 저장 경계까지 최근15.34초/update 기준 약84분이며 저장/인증·실제진단 시간은 별도다.
- 자동 순서: 기존 대기관리기4개를 identity 확인 후 승계 처리 → 원학습 경계 정지·전체 보호복사/SHA·계보 seal → 별도 경로 실제8GPU 네모드 비교 → 합격 증거를35k 계약에 결합 → 새 본학습 기동·부모+10 로그 확인 → 새 observer/native 평가 대기관리기 등록. 진단 결과를 본학습 토큰에 더하지 않는다.
- `horizon_retention.py`는 구/신 run **합계** NFS 최신3·로컬 과거최대15를 관리한다. 각 사본의 원계약 ID를 유지하고 최신3/공간 예산을 통합 계산한다. 보호 부모와 진단 근거는 정상 이력 개수 밖의 별도 예약이며 임의 삭제하지 않는다.
- 검증: 보존/감독기·인계15개, 저장/전환/활성화25개, 최신 전환/활성화11개 검사 통과. 시험 묶음은 중복되므로 합산 고유 시험 수로 표시하지 않는다. 작은 실제 CPU 프로세스·NFS/로컬 복사/삭제/SHA 및 mock 제어 흐름 검증이며 실제3B/8GPU 전환 통과가 아니다.
- 최신 용량 계산: NFS 향후 추가 peak551.752GiB, 현재 여유818.433GiB에서 안전200GiB를 제외한 잔여66.680GiB. 로컬 여유1454.472GiB, 공유 임시1024GiB·안전200GiB·향후 이력/복사 예약을 유지한다. 실행 직전 다시 준입하며 대형 전환의 실제 peak 측정은 아직이다.
- 실행 순서 조정: 구/신 통합 보존과 새 observer/native 관리기는 준비 완료. **최종35k 실제복원·통합 보존감사 관리기와 살아 있는 worker의 감독기 재인수는 아직 미완료**이며 학습과 병행 준비하고 늦어도34,500 이전 검증한다. 현재 새 감독기는 기존 worker가 살아 있으면 중복 기동을 거부한다. 이 제한을 정상 복구 검증 완료로 표시하지 않는다.
- 실패 시 해당 단계와 원본·부분 산출물을 보존하고 오류 이벤트를 남긴다. 자동 재시도·원계약 fallback은 하지 않으며 판단이 필요한 복구는 사용자에게 보고한다. 새 본학습 기동 후 관측 timeout만으로 학습을 재시작하지 않는다.

이 절이 아래의 과거 준비 순서·미구현 표기보다 우선한다. 전환 대기는 실제 기동됐지만 **35k 실제 실행 적용·최종 학습·복원·평가·추론 패키징 완료는 아니다**. 검증 근거: `runs/main_training_35k_plan_v1/authorized_transition_waiter_validation.json`.

### 15:11 UTC 병행 준비 갱신

35k 전용 최종 복원 worker(`train/final_horizon_resume_probe.py`)와 준비 도구(`scripts/prepare_final_horizon_resume.py`)를 별도로 구현했다. 실제 작은CPU DCP·RNG·독립프로세스 재개 및 합성35k 입구15검사 통과. 실제 운영 계획은 새35k run 활성화 후에만 발행한다. **최종 controller/공유 자원 준입/실제24rank 실행 결합·3B8GPU 검증은 아직 남았다.** 근거 `runs/main_training_35k_plan_v1/final_horizon_resume_preparation_validation.json`. 자동8,000 전환이 사용하는86개 코드/입력 SHA는 변경하지 않았다.

### 15:21 UTC 최종 복원 관리기 준비

`run_final_horizon_resume.py`에35k 최종감사·fresh입력/SHA·정확한새계약 재구성·통합공간/PSS준입·pidfd 소유프로세스 관리·24rank 실행증거·전체상태 비교를 연결했다. **29개 CPU/소량FS/합성 제어 검사 통과**, 실제3B8GPU 경로는 미실행이다. CPU해시는 GPU잠금 전에 수행해 native평가와 병행한다. 전환 성공 후 실제35k/native 계약으로 `runs/3b_35k_final_resume_v1` 준비·대기를 기동한다. 보호된 전환code/input86개는 불변이다. 근거 `runs/main_training_35k_plan_v1/final_horizon_controller_validation.json`. 통합 최종 보존감사·감독기 재인수 및 패키징 준비는 남아 있다.

### 15:33 UTC 통합 최종 보존감사 준비

`audit_horizon_retention.py` / `run_final_horizon_retention_audit.py`를 추가해 구/신 계약별 인증서구간과 두 저장소의 합계3+15,70개 전체저장이력·유실epoch·보호부모를 읽기전용 검증한다. 12개 실제작은CPU DCP/NFS/local 및합성 controller 검사통과, 실제3B 최종감사미실행. 추가local0/NFS기존1GiB/PSS16GiB/1200초/8hash worker 후보이며 정확한새run/native/resume 계약을 확인한뒤에만 production 준비·대기를기동한다. 근거 `runs/main_training_35k_plan_v1/horizon_retention_audit_validation.json`. 기존57k run을35k 최종완료로인정하지않는실제읽기전용거부도확인했다.

### 15:44 UTC HF 변환 준비

`inference/hf_export.py`에 native→HF Llama 변환과 FP32 임시 패키지 생성을 추가했다. 실제 `ModelConfig`에서 255개 모델 가중치의 키·shape를 산출하며 Q/K/V 누락, 비유한 값, 잘못된 tied head를 거부한다. untied `out.weight`, RMSNorm epsilon, GQA, RoPE theta를 보존한다. tokenizer JSON 원본 바이트를 유지하며 generic `PreTrainedTokenizerFast`로 metadata만 추가한다. BOS/EOS 자동 삽입과 chat template은 사용하지 않는다.

설치된 Transformers4.55.2를 실제 호출한 **CPU23검사 통과**: tiny tied/untied·KV1/2/6·eager/SDPA, logits/loss, KV cache와 greedy 생성, sharded Safetensors 저장/재로딩, 최종65536 tokenizer의24개 한영·수학·코드·Unicode/특수토큰 probe. FP32 허용오차는 atol1e-5/rtol1e-4. REF-EXPORT/X01의 Q 누락을 모델 생성 전에 거부한다. 근거는 `runs/main_training_35k_plan_v1/hf_export_preparation_validation.json`, `hf_export_tests_v1.xml`이다.

이 API는 **staged_not_released 임시 변환 도구**이며 운영 발행 관리기가 아니다. 실패 시 부분 디렉터리를 보존하고 재사용/덮어쓰기를 거부하며 `.done`을 만들지 않는다. 다음 결합 작업은 실제35k 최종 NFS 인증·입력/코드 계보·전체예산 준입 → DCP 모델 읽기 → FP32 staging → 실제3B native/HF 수치 비교 → 파일 SHA/fsync·동일FS 완료 발행 → 격리된 vLLM 실행 검증이다. 대형 peak RAM/디스크, BF16 수치/성능, 실제 vLLM/Ollama는 아직 미측정이다. 기존 패키지100GiB 예약을 재사용하고 추가로 중복 예약하지 않는다.

[vLLM 공식 지원 목록](https://docs.vllm.ai/en/latest/models/supported_models/)은 `LlamaForCausalLM`을 지원하지만 이 패키지의 실제 실행 성공을 대신하지 않는다. [Ollama 공식 import 절차](https://docs.ollama.com/import)는 Safetensors/GGUF 경로를 제공한다. 현재의 Digits+ByteLevel tokenizer가 Ollama에서도 동일한 ID를 만드는지는 별도로 검사해야 하며, 다른 알려진 tokenizer로 표기해 우회하지 않는다. 설치된 학습 환경을 변경하거나 학습 GPU를 점유하지 않았다. 새35k 실행은 여전히8,000 경계 대기 중이다.

### 15:54 UTC 수치검사·NFS 발행 방식 실측 반영

`inference/verify_package.py`가 저장된 HF 모델을 다시 읽고 CPU FP32 전체 vocabulary logits/NLL/cache/greedy 및 tokenizer를 비교한다. 실제 고정6프롬프트와1token/4096context 입력 생성기를 준비했다. 새16검사는 작은 모델에서 실제 실행했으며, 실제3B4096 수치검사나 운영 source 준입을 대신하지 않는다.

이 NFS는 no-replace directory rename을 지원하지 않아 최초 실제NFS 시험에서 EINVAL(22)이 발생했다. 패키지는 **새 final 디렉터리 exclusive 생성→동일FS payload hardlink→SHA/fsync→완료 명세 hardlink 원자 발행**으로 바꿨다. final 디렉터리가 먼저 보일 수 있으므로 소비자는 `release_manifest.json`과 전체 SHA가 검증된 경우에만 사용한다. 중간실패 사본/기존출력을 보존하고 자동 재시도·덮어쓰기·삭제를 하지 않는다. staging도 그대로 남으며 두 경로가 공유하는 내용은 불변이다. 공간 준입은 보수적으로 두 경로의 논리 크기를 모두 계산한다(FP32 weights 약12.00065GiB×2+metadata; 기존100GiB 예약 재사용). 실제 최종 데이터 크기/peak는 실행 전후 확인한다.

실제NFS/로컬 tiny 패키지·중단4지점·변조·공간/source guard 실패 fixture 포함16검사 통과. `runs/main_training_35k_plan_v1/hf_publication_preparation_validation.json`. **최종35k source 인증·자원준입 controller와의 결합 및 vLLM/Ollama 실제 실행은 아직 남았다.** 이번 준비는 운영 producer/전환86개 바인딩을 변경하지 않았다.

### 16:02 UTC 최종 원본 준입 연결

`inference/final_source.py`에 실제 활성화된35k 계약과 최종 native/resume/retention 의존성을 연결했다. 최종8rank/토큰 감사와 정확한 NFS 전체상태 SHA, 실제복원/native/보존 근거, fresh입력계약을 확인한 뒤에만 같은 실행에서 사용하는 `LiveSource` guard를 반환한다. 기존 원본 바인딩을 새해시로 덮어 숨기지 않으며 검사 뒤 원본/입력/근거가 바뀌면 진행하지 않는다. 저장된 attestation은 재개 시 fresh 검증을 대체하지 않는다.

16개 실제작은FS/합성의존성 검사 통과. 최종3B·8GPU 검증함수는 mock 경계이며 실제최종준입 완료가 아니다. 현재운영57k source를 거부하는 실제읽기전용 검사도 통과했다. 근거 `runs/main_training_35k_plan_v1/hf_final_source_preparation_validation.json`. export100GiB 기존예약·추가local0·PSS128GiB/7200초/CPU32~63·16thread 후보를 기록했으며 대형실측/최적값은 미확정이다. 다음 bounded 실행관리기·독립발행검증·서버감시 결합 후 실제35k 의존계약이 생기면 준비/대기를 기동한다.

### 16:12 UTC 변환 운영 관리기 준비

`scripts/export_final_horizon_hf.py`가 네 선행작업의 실제 생존/완료를 확인하고 fresh최종source→DCP→HF→수치→NFS발행→독립검토를 연결한다.7200초/전체PSS128GiB/기존논리100GiB·200GiB여유·공유local1024GiB를감시한다. 보수적병행RAM합계1040GiB. CPU32~63/16thread는미측정후보이고실제전체peak는아직없다. 실제작은micro DCP부터HF/NFS발행까지포함한11검사통과;35k준입/실제3B수치/production CLI기동을대체하지않는다. `runs/main_training_35k_plan_v1/hf_export_controller_preparation_validation.json`.

새35k 및최종세의존관리기를준비한뒤 `python scripts/export_final_horizon_hf.py --prepare` → `--run`으로대기하고준비시생성된 `artifacts/3b_base_35k_v1/job_spec.json`을기존registry에등록한다. 현재는 `hf_export_job_spec_candidate.json` 후보만있고production준비/기동/등록은아직이다. HF `.done`은변환단계완료이며vLLM/Ollama/전체Goal완료가아니다. 살아있는orphan/실패잔여물은자동재시도하지않고보존한다. 실제추론환경/사용명령/모델카드·품질한계보고는후속으로남는다.

## 확정 목표와 실행 후보

| 항목 | 35k 계획 |
|---|---|
| 학습 대상 | 기존 약3.22B Dense, tokenizer/mix·데이터 비율 유지 |
| 총 committed step / rank-update | 35,000 / 280,000(8rank) |
| 총 학습 토큰 | 73,400,320,000(스텝당2,097,152) |
| GPU / 실행 geometry | H200×8, seq4096, micro4, deterministic eager·bf16·FSDP2 유지 |
| 학습률 | 기존warmup1000·peak3e-4, 마지막15%인29,750부터35,000까지 선형 감쇠 |
| 최저 LR 비율 | 0.1 유지. 실제0-based 마지막update LR과종료경계 별도 검사 |
| 평가 / 완전상태 저장 | 250 / 500스텝 유지,35,000 최종평가·저장 필수 |
| 고정 생성 | 기존증거 이어받기,10k/15k/20k/25k/30k/35k, 최종중복실행 방지 |
| 새 본학습 후보 | `runs/3b_full_35k_v1` |
| 추론 패키지 후보 | `artifacts/3b_base_35k_v1/` |
| 보존 / 알림 | NFS최신3 + 로컬과거최대15 적응형, 서버30초 / 대화1시간+500step |

13:16 UTC 관측7,250step·136,736.341tokens/s 기준 남은27,750step의 **순수 연산 약118.22시간(약4.93일)**. 57k보다약93.73시간,40k보다약21.30시간 절약 추정이다. 실제 저장/평가/전환·최종복원·패키징 시간은 별도이며 완료일 보장이 아니다. 현행 고정6소스 NLL7,000의2.170988→7,250의2.163002로전소스개선. 35k/57k 실용품질 차이는 아직 미측정이며5000 생성의반복/산술/코드문제는 미해결이다.

## 1. 실행 전 전환 계약 완성

13:24 UTC 구현갱신:35k 공통목표와worker/감독기/bundle 연결, parent_stats 및probe/.done 누락수정,61CPU/소량FS검사와실제probe/7000meta검토통과. 근거 `runs/main_training_35k_plan_v1/target_integration_validation.json`. 아래3항의발견누락은수정했으며실제8GPU진단과운영전환은미완료다.

현재 producer와입력은 유지한다. 기존40k용 준비 코드는 부분 재사용하되35k가 검증됐다고 간주하지 않는다.

1. `horizon_worker_contract.py`, `monitored_horizon.py`, `supervise_horizon_training.py`, `prepare_horizon_bundle.py`의40k 고정값·최종경로·토큰수·생성일정을35k의하나의검증된목표계약에서유도한다. 전환검증은29,750 이전의부모에한정하고 기존LR과의전체prefix동일성,29,750/34,999/35,000 경계를검사한다. live57k의고정51코드와main.json은수정하지않는다.
2. 새35k bundle에정확한입력/코드해시·owner·관리경로·auth근거·공간/시간예산·재개절차를결합한다. `runs/main_training_40k_plan_v1` 및bundle_v1은과거준비증거로보존하고실행에사용하지않는다. 현재35k run/패키지후보경로는미생성이다.
3. 실제worker전체진입을검사한다. 최근코드검토에서새sealer receipt에worker가요구하는 `parent_stats` 연결이누락됐고, bundle의 `probe/.done` 복사도누락된것을발견했다. 이를수정하고해시·실제probe검증·부모선택/DCP로드까지연결검사한다. 입구schema시험만으로통과표시하지않는다.
4. 실제8GPU 진단entry/검증기와bounded 실행controller를준비한다. 같은완전상태부모에서model/optimizer/8rank RNG 정확복원,기존57k와새35k의다음update동등성,새계약연속2update 대1update→프로세스종료→재개1update를비교한다. 소스선택/절대step/LR·전체상태·RNG를대조하고진단토큰은본학습목표에더하지않는다.
5. observer·최종native/실제복원/보존감사controller·진척/ETA·완료조건을35k로준비한다. 실제worker가남은감독기재시작의adoption경로도검증한다. 기존57k 대기프로세스는실제전환때PID/start-time에묶어교체한다.

완료조건:35k CPU/소량FS검사통과,실제8GPU진단을실행할구체bundle·명령·예산과복구수단준비. 여기까지는본학습중단없이수행한다. 별도작업은CPU/메모리/디스크실측예산안에서진행하며훈련GPU를경합시키지않는다.

### 최신 준비 상태 (2026-11-08 14:42 UTC)

- `scripts/audit_horizon_training.py`: 부모/자식 계약ID와 절대step 경계를 각각 검사하고 합산한다. 부모 이후 자식 coverage·재실행량·평가/생성·최종280,000 rank/update 및73,400,320,000tokens를 검사하며 원기록을 수정하지 않는다. 실제7,500 원본 trace를 기존 감사기와 재대조해 동일함을 확인했다.
- `scripts/evaluate_final_horizon.py`:34,500 CPU 평가입력 준비→35,000 통합감사→유휴GPU lock→native 한/영PPL·6고정생성 경로를 별도 구현했다. 기존8GiB 예약을 중복 합산하지 않고 구/신/진단/패키지 전체 미래 예산 및 공유 임시1TiB를 확인한다. 현재 기동하지 않았다.
- 최신 **17개 CPU/fixture 검사 통과**. 실제 작은CPU DCP 저장/복원과 native평가, 합성35k 전체rank이력·누락/중복/계약/완료증명·공간검사 범위다. 실제3B 최종평가 또는35k 최종8GPU재개/보존관리기까지 완성됐다는 뜻은 아니다. `runs/main_training_35k_plan_v1/final_audit_native_validation.json`.

- 경계 전환 wrapper의 통합 검사 **6개 통과**: 실제 작은 CPU 프로세스10개/pidfd·파일 잠금·보호복사/전수SHA·정지 후 계보 seal을 연결했다. 입력 변조·공간 부족·보존 미정리·payload 손상·종료 후 seal 실패도 확인했다. 원본 감사/payload·용량·생산자 식별은 fixture이므로 실제3B/8GPU 전환 통과는 아니다. 근거 `runs/main_training_35k_plan_v1/boundary_integration_tests_v1.xml`.
- 실제7,500 체크포인트는60,000 rank/update·15,728,640,000tokens, NFS3+local12 전체SHA·잔여물0을 확인했다. 실제 GPU 복원은 별도다. 현재 생산자는 원57k 계약을 유지한다.
- 아래의 기존 CPU/진단 준비 증거를 이어 사용한다. 당시 남은 항목은 상단의 최신 승인·실행 순서로 갱신했다. 실제8GPU 검증 통과 전에는 새35k 학습을 기동하지 않는다.

- 35k 목표·LR 경계·입력 묶음의 기존61개 CPU/소량 파일시스템 검사가 통과했다.
- 진단용 전체 체크포인트 인증·소스 ledger 비교·실행 입구의 최신14개 검사가 통과했다. 작은 CPU DCP를 실제 저장/복원했고, 손상·model-only·RNG 누락·계약/소스 불일치를 거부했다. 증거: `runs/main_training_35k_plan_v1/probe_certification_validation.json`.
- 실제 NCCL/H200 rank 실행 증명을 남기는 코드는 준비했지만 GPU 실행은 아직 검증하지 않았다. CPU 통과를 실제3B/8GPU 재개 통과로 간주하지 않는다.
- 다음 순서는 **진단 spec/제한된 실행 controller 완성 → 저장 경계 인계·부모 보호 → 실제8GPU 비교 → 35k 운영 전환**이다. 구/신 통합 보존은 구현·시험했으며, 최종 복원/보존 controller와 감독기 재인수의 남은 검증 시점은 상단 최신 실행 순서를 따른다.
- 현재 학습은 기존57k 계약으로 계속 실행 중이며, 목표35k 변경과 운영 적용 완료를 구분한다.

## 2. 저장 경계에서 기존 진행분 인계

승인된 대기관리기는 미래8,000스텝의 인증·보존 정리 완료를 기다린다. 경계를 놓치거나 원프로세스 identity가 바뀌면 임의로 다른 부모를 고르지 않고 중단·보고한다.

- PID/start_ticks/실행명령/실제rank를확인하고정해진저장경계에서worker·감독기를조정한다. 새cp내용인증·보존transaction완료·보호복사에필요한작업이끝나도록순서를검증한다. 두생산자가같은GPU/namespace를동시에사용하지않는다.
- 부모를NFS새run의보호경로로임시복사→목록/크기/SHA/fsync→동일FS완료발행한다. 원본meta·optimizer·RNG는수정하지않는다. 기존8rank원본ledger/trace를정지후다시감사해sealed계보를발행한다. live중만든lineage는그대로재사용하지않는다.
- 실제8GPU 연속/분할진단을실행하고검증된증거를새35k계약에결합한다. 실패하면원본/실패산출물을보존하고중단·보고하며반복재시도하지않는다. 승인된정상복구는동일원계약의인증NFS사본으로복구할수있도록준비한다.
- 새run은부모의절대step에서계속한다. 저장되지않은진행분은로그에서확인된최소량과미확정범위를기록하고0이라고단정하지않는다. 현재원계약57k와변경목표35k표시를실제기동확인뒤갱신한다.

기존사용자본학습/이동삭제승인은유지하되,이번계획은외부업로드·다른자료삭제승인을추가하지않는다. 구체실행계약을제시하고기존실행확인규칙을따른다. 이미승인된정상작업을매번재확인하지않는다.

### 경계 전환 실패 시 원계약 복구 절차

복구의 원본은 `runs/3b_full_v1`의 변경하지 않은 `contract.json`, `main.json`, 인증된 NFS 체크포인트와 원8rank ledger다. `boundary`의 state/failure/signals 기록과 보호사본 receipt는 새 전환 경로에 보존한다. 관측 timeout이나 예외 문자열만으로 원프로세스가 종료됐다고 판단하지 않는다.

| 관측한 상태 | 처리 및 재개 조건 |
|---|---|
| 신호 전 사전검사 실패 | 원10PID/start_ticks·명령과 학습 로그 진행을 재확인한다. 정상 학습은 계속 유지한다. 실패한 준비 경로를 지우거나 같은 경로로 자동 재시도하지 않는다. |
| 복사/검증 실패, 원10개 모두 생존·일시 정지 | 종료 신호 전송 여부와 새로운 자식, 원계약/입력, 저장 상태를 확인한다. 사용자의 예외 판단 후 같은 pidfd로 원프로세스 재개하거나 인증된 체크포인트 재시작을 선택한다. 오래 정지한 NCCL 작업의 메모리 상태 재개를 보장하지 않는다. |
| 일부 원프로세스만 종료 | 남은 프로세스가 GPU를 보유한 상태에서 새 학습을 시작하지 않는다. 실제 identity와 소유 범위를 확인하고 사용자 판단에 따라 남은 원프로세스를 종료한 뒤 NFS 재개를 준비한다. 다른 job이나 PID 재사용 대상에 신호를 보내지 않는다. |
| 원10개 종료, seal/8GPU 진단 실패 | 원NFS와 검증된 보호사본·실패 증거를 유지한다. 원/진단 GPU 프로세스 부재, 잠금 해제, NFS 실제 전체상태·SHA·원계약과 자원 준입을 확인한 뒤 동일 원계약 재시작을 검토한다. 진단 성공으로 기록하거나 목표35k를 적용했다고 표시하지 않는다. |

원프로세스가 전부 종료된 경우의 **기존 감독기 재시작 후보 명령**은 `python -u scripts/supervise_main_training.py --run-dir runs/3b_full_v1 --run`이다. 종료된 worker의 `--adopt-receipt`를 재사용하지 않는다. 이 명령은 검토용이며 이번 준비에서 실행하지 않았다. 실행 시 기존 감독기는 원51코드/입력·framework·main 설정을 대조하고 NFS 인증을 다시 확인하며 새 attempt로 재개한다. 기존 승인 범위의 보존 관리도 실행될 수 있다.

이 복구를 실제 전환의 자동 fallback으로 연결하기 전에 다음을 검증한다.

1. 원/신 run·진단·보호사본이 함께 남은 용량을 재측정하고 추가예산을 반영한다. 기존 감독기의 자기 run 예산만으로 전환 잔여물까지 확보됐다고 간주하지 않는다.
2. 원run의 미완성/미검증 저장 흔적을 별도 증거로 보존한다. 충돌 경로를 덮어쓰거나 삭제해 재개를 통과시키지 않는다. 불완전 파일이 있으면 구체 보존 조치와 경로를 먼저 검토한다.
3. observer·최종평가/복원/보존 대기관리기의 실제 PID/start_ticks와 대상 계약을 확인하고 의도된 전환/복귀 이벤트를 기록한다. 57k 관리기와35k 관리기를 동시에 최종 작업 소유자로 두지 않는다.
4. 재개 attempt가 인증된 절대step에서 이어지는지 실제8rank 기록으로 확인한다. 저장 이후 소실된 진행분은 로그로 확인한 최소량과 미확정 상한을 구별하며, 전환/진단/복구 시간과 재실행 토큰은 전체 학습량과 별도 집계한다.

현재6개 통합 검사는 실패 시 원본/보호사본 보존까지 증명한다. **위 후보 명령의 실제3B 재기동·CUDA 복구 성공은 아직 검증하지 않았다.**

## 3. 35,000까지 학습하면서 패키징 준비

- 29,750 이전은검증된기존LR,29,750~35,000은새감쇠일정을적용한다. `stop_after=35000`만설정해원57k감쇠전에끊는방식은사용하지않는다. 수치비유한검사·필수내용검증·기존평가/저장주기는유지한다.
- 10k/15k/20k/25k/30k/35k의생성품질과언어/수학/코드평가를기록한다. 손실감소와실용품질성공을분리한다. 단축에따른남은품질위험을최종모델카드에명시한다.
- REF-EXPORT/X01기준으로native→HF변환기·tokenizer포장·실행스크립트를CPU에서준비한다. untied lm_head를보존하고키/shape/dtype·RoPE/GQA/SwiGLU를대조한다. 작은모델FP32 logits atol1e-5/rtol1e-4,실제3B BF16 허용오차를사전정의한다. 한/영/숫자/수학/코드/Unicode/token ID·decode를검사한다.
- 학습환경을변경하지않는독립환경에런타임버전을고정한다. GPU성능/호환측정은학습GPU해제후진행한다. 34,500까지최종35k 검증·변환·실행명령의준비상태를점검한다.

## 4. 정확히35,000에서 학습 종료·최종 검증

1. 원본부모+자식의중복없는280,000 rank/update·73,400,320,000tokens·소스별투입량을검증한다. 재실행/진단량은따로기록한다.
2. `runs/3b_full_35k_v1/training/ckpt_0035000`의model/optimizer/8rank RNG/meta/입력·코드계약/계보·전체SHA/완료인증을검증한다. 새완료본인증전에기존유효본을정리하지않는다.
3. 별도진단경로에서최종체크포인트의실제8GPU복원·재개를검증한다. 본학습원본과35k목표를늘리지않는다.
4. native한·영 PPL/고정생성·6소스평가,보존상태/잔여물/공간,처리량/총시간/장애이력을확인한다. 공식벤치마크는별도이며미측정을통과로표시하지않는다.

## 5. 추론 패키징과 실제 사용 테스트

필수경로는 **native완전상태→HF safetensors→vLLM 실제추론**이다. 사용자원요청의vLLM또는Ollama에맞춰vLLM을우선완료하고Ollama호환성결과도보고한다.

| 산출물 | 내용 / 통과 조건 |
|---|---|
| `artifacts/3b_base_35k_v1/hf/` | 가중치shard·config·tokenizer·special tokens·generation config·SHA manifest. native와token ID 및logits/NLL비교통과 |
| `artifacts/3b_base_35k_v1/runtime/` | 고정환경/재현실행스크립트·localhost vLLM completion API. 한/영/수학/코드·streaming·EOS·길이/context4096 검사,latency/처리량/VRAM실측 |
| `artifacts/3b_base_35k_v1/ollama/` | Safetensors직접import 또는GGUF호환검사. 자체Digits+ByteLevel전처리일치필수. 가능한경우Modelfile·실제create/run/API시험 |
| `artifacts/3b_base_35k_v1/reports/` | 변환전후수치/실제출력·원본cp/코드/환경근거·품질한계·완료보고서/모델카드 |

vLLM은단일H200부터측정하고추가병렬화는처리량/지연이개선되는경우채택한다. Ollama는현재공식문서의Safetensors직접import부터저비용검토하고필요시GGUF F16/BF16→Q8_0/Q4_K_M을비교한다. 자체토크나이저를지원하는척다른이름으로바꾸지않는다. 양자화는비양자화기준품질/속도/용량을확인한뒤선택한다. vLLM또는Ollama의지원목록에있다는사실로사용자모델의실제통과를대신하지않는다.

Base completion모델로포장하며미학습채팅template나SFT성능을주장하지않는다. SFT·공식벤치마크·외부모델업로드는자동실행하지않는다. 모델파일을저장한것과실제로사용가능한패키지검증을구별한다.

## 공간·보존·실행 경계

- NFS최신검증3개,로컬과거최대15개이며공간압박때승인namespace의검증된오래된이력부터축소한다. 구/신run 합계로 관리하는 구현과 소형 시험이 완료됐다. 실제 운영 적용은8,000 경계 전환 이후이며 각각3+15를 추가 보장하지 않는다. 기존데이터/진단/복구증거의추가삭제를전제로하지않는다.
- 종전40k초안의향후NFS추가549.752GiB는참고예산이며35k실행직전에현재사용량과진단출력개수/임시복사peak로재산정한다. 현재원본/로컬이력을전부보존할경우신규로컬15개추가공간은보장되지않는다. 진단설계가변하면예산도변경한다.
- full cp약36.011GiB,추론산출물후보100GiB,로컬공유임시1024GiB와각FS최소200GiB여유. `/`와`/tmp`는같은FS이므로합산하지않는다. 실제배정된파일을향후예산으로중복계산하지않는다. 공간부족/부분복사/잔여물초과는중단·보고하고임의정리하지않는다.
- 정상보고1시간+500step,오류/중단/단계완료/직접조회즉시. 자원표포함,서버30초감시는모델호출없이유지한다. 예외판단이필요한해당작업만대기하며대화종료후자동메시지를보장하지않는다.

## 완료 판정

**35k 실제학습 + 최종NFS전체상태·SHA + 실제8GPU복원 + native평가/생성 + 보존감사 + HF수치/tokenizer검증 + vLLM실제실행 + Ollama호환결과(지원시실행) + 재현패키지/한국어보고서**를모두근거로확인한다. 하나라도필수검증이누락되면Goal완료로표시하지않는다.

현재는35k 전환 구현·통합 검증 준비 단계다. 기존부분구현·CPU검사·실제7500원본감사증거는재사용범위에서만참고하고35k 실제8GPU전환/실행완료로간주하지않는다. 개발보조모델은기존WORKFLOW의계약변경/최종검토Astra/High,정상감시Luna/Low권장을유지한다. 주세션자동변경·서브에이전트위임은하지않는다.

공식확인(2026-11-08): [vLLM 지원 모델](https://docs.vllm.ai/en/latest/models/supported_models/), [Ollama 모델 가져오기](https://docs.ollama.com/import). 실제설치버전에서지원과동작을다시검증한다.

### 16:26 UTC 격리 추론 환경과 토크나이저 사전 확인

- `scripts/prepare_vllm_environment.py`로 vLLM0.11.2/Transformers4.57.6/PyTorch2.9.0+cu128 후보를 학습 환경과 분리해 준비 중이다. 최신 버전/최적 성능 확정이 아니다. 공식 버전 문서의 CUDA12.8·별도 환경 권고와 현재 driver580.126.20을 확인했다. CPU4~7/thread2, CUDA 비노출, PSS16GiB/2400초, 공유local1024GiB 안의64GiB, 증거128MiB 및 양쪽200GiB 안전 여유를 적용한다.
- 최초 시도는 공식 PyTorch CDN(`download-r2.pytorch.org`)과 NVIDIA wheel 주소가 허용 목록에서 누락돼 설치 전에 중단됐다. resolver는171개 wheel 해시 목록을 완성했으며 원본 코드/실패기록/환경을 보존했다. 허용 주소·SHA/버전 검사를 보완하고 새 `serving_environment_v2`와 로컬 `serving_vllm0112_v2`에서 같은 resolution을 재사용한다. 임의 호스트/잘못된SHA/고정버전 변경 거부도 실제 확인했다. 기존 학습 패키지·전환86개 바인딩은 유지한다.
- 실제 tokenizer는 Digits(individual)+ByteLevel이다. 이를 ByteLevel만으로 단순화한 **대체 구현 시험**18개 중2개에서 공백/CRLF 뒤 숫자의 token ID가 달랐다. `ollama_pretokenizer_surrogate_v1.json`. 이는 실제 Ollama 실행 결과가 아니며 설치된 고정 버전의 변환·토큰 일치 검사가 여전히 필요하다. 공식 source의 검색 인덱스는 Split regex digest 기반 default 분기를 보였으나 현재 raw/blob URL은404여서 최신 설치 구현을 확정하지 못했다. 토크나이저 이름/메타데이터를 바꿔 호환을 가장하지 않는다.
- 근거: `runs/main_training_35k_plan_v1/serving_environment_v1/` 및 `serving_environment_v2/`. 설치 완료·실제GPU vLLM 추론·실제3B 변환은 별개 판정이다.
- 공식 참고: https://docs.vllm.ai/en/v0.11.2/getting_started/installation/gpu/ , https://download.pytorch.org/whl/cu128/torch/ , https://github.com/ollama/ollama/blob/main/convert/tokenizer.go .

### 16:35 UTC 실제 경계 실패와 복구 대기

8,000 NFS 인증·보호복사/전수SHA까지 통과했지만 감독기 종료 관측 중 identity 오류로 중단했다. 감독기는 종료,원워커9개는 정지 상태다. 새35k 활성화/8GPU진단은 미실행. 기존 실패기록과86개 동결파일을 보존하고 새 pidfd 종료관측 helper의4개 작은실행검사를 통과했다. 상세복구선택/현재자원은 STATUS 및 `runs/3b_35k_transition_v1/recovery_review_v1/proposal.json`을 따른다. 사용자 예외판단 응답 전 원프로세스 추가 신호/재기동은 하지 않는다. 격리vLLM 후보설치·작은실제HF버전간검사·10tokenizer사례는 통과했으며 실제3B/GPU 추론은 여전히 남았다.

### 16:45 UTC 복구 도구와 실행 계획 준비

`scripts/recover_horizon_boundary.py`의12개CPU/프로세스검사가통과했고, 실제 `--prepare`로9개정지프로세스·양쪽checkpoint전수SHA·owner/용량을확인했다. 실행계획은 `runs/3b_35k_transition_v1/recovery_review_v1/execution_plan.json`, 미승인템플릿은같은경로 `approval_required.json`이다. 템플릿은실행입구에서거부됨을확인했다. 사용자의복구선택후에만그응답과정확한계획SHA를별도승인기록으로남겨 `--execute --plan <execution_plan> --approval <실제승인기록>`을사용한다. CPU8~15/OMP·OPENBLAS2/CUDA비노출설정을유지한다.

이도구는기존정지워커를종료하고원8000인계seal까지수행한다. 새운영출력 `recovery_execution_v1`은기존전환1GiB예약안에있고실패시같은경로를자동재실행하지않는다. 통과후원래의 `prepare_horizon_probe`→`run_horizon_probe` 실제8GPU비교→`activate_horizon_training`→새35k감독기기동순서를이어간다. 새모델을기동했다고표시하기전실제로그진행을확인하며최종resume/retention/HF관측기는활성화된계약으로준비한다. 복구증거를실제8GPU/최종35k완료증거로대체하지않는다.


## 2026-11-08 23:01 UTC — 35,000스텝 전환 복구 완료·실제8GPU 검증 통과·본학습 재개

- 사용자 최종 ‘35000 스텝 전환 복구 진행’ 승인으로8000 인계 복구를 마쳤다. 기존 실패 증거와 원본을 보존했다. 신규 복구 도구 `scripts/horizon_exit_wait.py`, `scripts/recover_horizon_boundary.py` 및 관련12시험 통과에 이어 실제 원워커 종료·양쪽 전체 SHA·계보 seal을 검증했다. 근거: `runs/3b_35k_transition_v1/recovery_execution_v1/report.json`.
- 실제 H200×8 baseline/continuous/first/resumed 검증은 **663.50초**(controller monotonic)로 완료됐다. 부모 복원과 분할 재개 모두 model255+optimizer765=1020 tensor 및8rank RNG 정확 일치, 다음 update·연속/분할 결과 동일. 진단 실제12,582,912 tokens는 본학습 투입량에서 제외한다. `runs/3b_full_35k_v1/horizon_probe/report.json`, `comparisons.json`. 이는8000 경계 검증이며 최종35000 복원 완료가 아니다.
- 검증 근거와 계약 SHA를 재확인하고 `runs/3b_full_35k_v1`을 기동했다. supervisor989705 / torchrun989896 실제 명령·생존 확인, 최신 로그 **8020/35000 (22.91%)**. 인증된 체크포인트는8000, 다음 정규 저장8500이다. target73,400,320,000 tokens, decay29750→35000, 평가250/저장500 설정 유지. activation.json의 not_launched는 활성화 당시 기록이며 이후 실행 증거는 `runs/3b_35k_transition_v1/recovery_review_v1/actual35k_recovery_completion.json`이다.
- 관측기991009 및 최종 native991010·실제복원991011·보존감사991015·HF변환991016을 정확한 새 계약으로 준비·기동·서버30초 감시에 등록했다. 모두 실제 PID/명령 생존 확인; 최종 작업은 대기 중이며 완료로 표시하지 않는다. 등록/로그: `recovery_review_v1/started_final_controllers.json`. 일반 보고1시간/500step, 최종 알림35000으로 갱신했다.
- 보존은 원본/신규 합산 **NFS 최신3+로컬 과거 최대15** 및 공간 적응 정책이다. 현재 NFS7000/7500/8000, local500~6500의13개. 보호 부모 및 진단 사본은 별도 예산이며 삭제하지 않는다. 새8500에서 실제 새run의 첫 이동/정리 결과를 확인할 예정이다.
- 23:00:04UTC 실측: CPU8.80%/96cores; RAM1859.16/1920GiB(파일캐시1818.79GiB); GPU0~7 각각99%, VRAM0=98.67GiB/1~7각96.42GiB(각140.40GiB); NFS사용1297.36/여유565.29GiB, local사용1374.63/여유1408.26GiB; OOM/kill0. /와/tmp 동일FS 합산하지 않았다. 현재 점유량은 실측 snapshot이며 전체 단계 최대사용량으로 오인하지 않는다. 신규 main PSS 관측 peak 약14.98GiB(23:00 state); 공유파일캐시와 별도다. 향후 공간 peak는 기존 통합준입으로 감시하며1024GiB local scratch/저장소별200GiB 안전여유 유지.
- 실패/공간부족/수치발산 시 기존 보호 중단과 증거 보존을 유지하고, 판단이 필요한 추가 복구는 사용자에게 보고한다. 원전환 실패 state를 성공으로 덮지 않았다. 현재 서버 학습과 관리기는 정상 실행 중이나 Goal서비스의 마지막 상태는blocked이며 도구에active 변경기능이 없어 서비스 자동 재개를 주장하지 않는다.
- 남은 작업:35000까지 본학습, 최종 전체상태 인증·실제복원·native평가/생성·보존감사, HF 수치 동일성, 실제3B vLLM 추론 및 Ollama 호환성/가능시 실행검증. 반복생성·산술·코드 품질위험은 미해결이며 loss 감소와 실용품질을 구분한다. Goal은 미완료. 다음 정상운영 권장 **GPT-6 Luna/Low**, 새 검증Sol/Medium·복잡한 장애/최종감사Astra/High; 실제 앱 모델 자동변경 없음.



## 2026-11-08T23:24:33.618562+00:00 — 최종 vLLM 관리기 준비·40시험 통과·실제 대기 등록

- `scripts/run_final_vllm.py`, `inference/vllm_worker.py`, `tests/test_final_vllm_controller.py` 추가. 기존 최종HF 관리기의 완료 내용·전체payload SHA·source/diagnostic stat/계약·실제worker receipt를 검토한 뒤 공용GPU lock과실제nvidia-smi 비점유를 확인한다. 격리Python에 정확한 admission/contract/manifest를 전달하고 새runtime_package gate로 worker가 fresh SHA를 다시검증한 뒤12입력/두입구 vLLM 생성검사를 실행한다. 독립최종검토가 실제PID/start_ticks/owner/명령·버전/GPU·출력/해시·자식종료를확인해야 완료증명을발행한다. Goal/품질/Ollama완료와구분한다.
- 실행계약: CPU8~15/thread4, H200 한장·bf16/eager·context4096·KV예산gpu_memory_utilization0.25·prefix cache비활성,1800초/owned PSS64GiB, 증거1GiB(기존export100GiB안), 격리환경+cache64GiB(공유local1024GiB안). HF offline·usage stats비활성·고정버전이며 실행중학습환경은변경하지않는다. v0.11.2 설치코드의V1 engine_core.shutdown을명시호출하고소유자pidfd로자식종료를감독한다. 실제GPU최적성/peak는미측정이다.
- **40passed/0failed/0skip,7.94초**: 관리기16+출력12+패키지12. 실제live HF 대기확인/미완료source거부, 실제작은orphan프로세스보존,잘못된worker환경거부,시간·메모리·source·결과실패보존,통합예산중복방지,최종verifier 전완료미발행,owner/명령/남은자식/coverage/NaN/text-token변조거부를검사했다. 관리기lifecycle의GPU/source/실제추론실행은mock이고verifier 성공receipt는합성이다. 작은실제HF발행/격리-S내용검증은포함하지만최종3B GPU검증이아니다. `final_vllm_preparation_tests_v3.xml`과 `runs/main_training_35k_plan_v1/final_vllm_controller_preparation_v1.json`.
- 실제 `--prepare`로현재환경/공유예산을통과한정확한출력은 `artifacts/3b_base_35k_v1/vllm_runtime_v1`. 준비SHA c6d52147693e7f4e4fa292a1056fc4809f7262c0b979298887238376dae4ac62. 추가runtime1GiB는export100GiB에포함;준입당시NFS향후추가299.609GiB+안전200GiB 제외여유65.677GiB. 합산PSS1104GiB를cache-aware준입했다. 이는가능용량계산이며최대실측사용량이아니다.
- 새관리기 **PID993877** 실제명령/생존·`await_final_hf_export`확인, job `3b_35k_final_vllm_v1`을기존30초watcher에등록했다. 아직supervision/worker/engine이없으며GPU추론은시작하지않았다. 기존실패attempt/잔여물은보존·자동재시도거부,새예외판단규칙유지. 기존main70/export85+새runtime89code SHA불일치0.
- 본학습8110/35000,latest인증8000/다음8500. 원main989705/torchrun989896·관측기991009·최종4관리기생존. 23:23:35UTC CPU8.87%/96,RAM1859.36/1920GiB(cache1818.80),GPU0~7각99%,VRAM0=98.67/나머지96.42GiB,NFS사용1297.36/여유565.29GiB,local사용1374.63/여유1408.26GiB,OOM/kill0. /와/tmp합산없음.1시간/500step알림하한은변경하지않았다.
- 다음: 실제Ollama/GGUF tokenizer지원/변환경로검토·사용문서준비,8500저장/통합보존검토,승인된35k/최종검증의연속진행. 이전turn은내용gate준비progress,이번은관리기구현/40검증/실제대기등록progress. Goal active/미완료. 새검증준비권장Sol/Medium유지.

