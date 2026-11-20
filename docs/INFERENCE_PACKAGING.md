## 현재57k 실행 연결 상태 (2026-11-10 UTC)

`scripts/run_completed_training_followup.py`가 `runs/3b_57k_transition_v2/completed_training_storage_v2_followup/`에서 실제 학습 완료를 기다린다. 57,000완료 감사·학습프로세스 부재·공간준입 후 native→8GPU복원→보존→HF→vLLM offline/API 순서로 진행한다. CPU83검사와 관리기기동은 확인했지만 최종3B모델 실행은 아직 미시작이다. 원본학습계약을 유지하는 별도 공간예산 및 환경전체60GiB상한이 실행명령에 반영됐다. 상세근거는 STATUS와 `followup_migration_preparation_v9.json`이다. 기존35k관리기는 종료됐으며 아래35k기동설명은 과거기록이다.

Ollama/GGUF는 아래 고정버전·원토크나이저의 제한된 근거를 유지한다. 2026-11-10 문서정렬에서는 새 릴리스 조회·파서/변환기 재실행·모델변환을 하지 않았다. 최종시점의 지원확인과 사용결론은 남아 있다. 기존결과를 모든버전/백엔드의 미지원으로 확대하거나 HF/vLLM의 실제성공을 미리 표시하지 않는다.

## 현재57k 사용자 문서 초안

`runs/3b_57k_transition_v2/usage_draft_57k_v2/`에57k MODEL_CARD/USAGE 초안,localhost completion/streaming JSON,실제환경lock,명령/입력SHA,소스별기대투입량을준비했다. 아래35k초안은과거기록이다. 새초안은현재server_arguments와일치하며포트만사용자용8000으로표시했다. 같은환경·동일옵션의기존CPU CLI/schema검증을재사용했고변경된경로/alias의실제3B기동은미수행이다. 최종학습/저장/복원/native/HF/API검증후확정하며현재학습중사용명령을실행하지않는다.

57k기준코드기대노출은약2.9834회,위키한국어2.0393회,수학1.2275회다. 이는가중치×목표토큰/사용가능토큰계산이며실제소스별committed장부값은미확정이다. NLL감소를능력성공으로표시하지않고실제10000품질위험을연결했다. 근거는해당report.json/validation.json이다.

## 2026-11-09 UTC 호환성 근거 재확인

[공식 최신 릴리스](https://github.com/ollama/ollama/releases/latest)는 재조회 시에도 [v0.40.15](https://github.com/ollama/ollama/releases/tag/v0.40.15)였다. [공식 가져오기 안내](https://docs.ollama.com/import)는 Safetensors와GGUF 경로를 구별한다. 이 일반 지원 안내가 본 프로젝트 tokenizer의 지원을 보장하지는 않는다.

고정commit `02480ca2a47153e1f9c31cbe1e05536da97ce1ba`에서 parser·server/create·runner 원문3개58,470bytes를 별도로 받아 SHA256을 기록했다. parser/server는 이전 검토 원문과 동일하며, 원 tokenizer의 Digits→ByteLevel 및 SHA도 그대로다. 기존 실제llama.cpp 인식 거부 증거는 재사용했고 같은검사를 반복하지 않았다.

`mlxrunner` parser의 Digits 거부는 **해당 코드 경로의 소스 근거**다. 이를모든Ollama backend/version 또는Linux전체의 실행 실패로 확대하지 않는다. [runner 소스](https://github.com/ollama/ollama/blob/02480ca2a47153e1f9c31cbe1e05536da97ce1ba/mlxrunner/runner.go)는Metal과CUDA를모두언급하므로이름만으로Apple전용이라고판정하지않는다. 로컬PATH에Go가없어parser컴파일/실행도미수행이며새toolchain을설치하지않았다.

현재결론은 **원tokenizer를유지하는검증된Ollama경로미확보**다. 전체Ollama/최종모델실행은미수행,최종시점호환성판정은남아있다. 강제전처리교체/이름등록없이실제HF/vLLM검증을먼저완료하며그성공또한아직대기다. 근거 `runs/3b_57k_transition_v2/ollama_scope_review_v1/report.json`과`sources.json`.

# 35,000스텝 모델 추론 패키지와 호환성

현재는 본학습과 최종 검증 대기 단계다. 최종 HF 가중치와 실제3B vLLM 추론은 아직 완료되지 않았다. 아래 경로와 사용 조건은 실행 계약이며 배포 완료 보고가 아니다. 최신 결과는 STATUS와 각 관리기의 실제 보고서를 따른다.

## 최종 산출물과 검증 순서

1. `runs/3b_full_35k_v1/training/ckpt_0035000`: 35,000 누적 업데이트의 완전 재개용 원본. 모델·optimizer·8rank RNG·계약·ledger 인증이 필요하다.
2. `runs/3b_35k_final_native_v1`, `runs/3b_35k_final_resume_v1`, `runs/3b_35k_final_retention_v1`: 실제 native 평가·8GPU 복원·통합 보존 감사.
3. `artifacts/3b_base_35k_v1/hf`: HF FP32 모델, 원본 tokenizer, config, 변환·수치 검증 결과와 `release_manifest.json`. `.copying`은 사용하지 않는다. HF 패키지는 optimizer/RNG를 포함하는 학습 재개용 체크포인트가 아니다.
4. `artifacts/3b_base_35k_v1/vllm_runtime_v1`: 실제 격리 GPU 추론의 설정·프로세스 근거·생성 결과·최종 검사. 완료 marker만 읽지 않고 계약/보고서 SHA·worker 실행·최종 source 인증과 원본 토크나이저를 대조한다.

현재 관리기들은 이미 기동돼 있다. 상태 조회 후 새 관리기를 중복 기동하지 않는다. vLLM 관리기는 HF 최종 검증과 GPU 잠금/실제 비점유를 확인한 뒤에만 GPU를 사용한다.

## vLLM 실행 계약

검증 환경 후보는 vLLM0.11.2 / torch2.9.0+cu128 / Transformers4.57.6 / tokenizers0.22.2이다. 정확한 환경 경로는 `runs/main_training_35k_plan_v1/serving_environment_v2/report.json`의 `venv`를 따른다. 학습 환경과 분리하며 버전·실행 코드 해시가 달라지면 재검증한다.

실제 worker 명령은 완료된 `vllm_runtime_v1/supervision/handle.json`과 `result.json`에 기록된다. 준비된 진입점은 다음과 같다. 이 명령은 현재 기다리는 동일 관리기를 다시 기동하는 용도가 아니다.

```bash
python scripts/run_final_vllm.py --run
```

고정 설정은 GPU0 한 장, bf16, tensor parallel1, eager, 최대4096토큰, batch 최대12, GPU 메모리 비율0.25, prefix cache 비활성이다. CPU8~15/4thread,1800초/PSS64GiB, 원export100GiB 안의 증거1GiB, 공유임시1024GiB 안의 격리환경+cache64GiB로 제한한다. 실제 최대 사용량과 최적 성능은 실행 결과로 확인한다.

검증기는 고정 한·영·수학·코드6개, Unicode/숫자/개행/특수문자4개,1token/4095+1경계 총12개를 검사한다. 토큰 입력과 문자열 입력의 tokenizer ID·greedy 출력, finite logprob·표적 coverage를 확인한다. 고정길이 진단의 `ignore_eos=True`는 일반 사용자 생성 기본값이 아니다. prompt NLL은 smoke 측정이며 holdout PPL이나 공식 벤치마크가 아니다.

이 모델은 Base 모델이다. 검증되지 않은 chat template을 추가하거나 지시 수행 성능을 보장하지 않는다. HF FP32 변환 동일성과 vLLM bf16 실행 검증은 구분한다. 양자화 패키지는 별도 변환·품질 검증 전에는 제공 완료로 표시하지 않는다.

## localhost API 검증 준비 — 실제 서버 실행 전

`inference/serving_validation.py`는 127.0.0.1의 명시적 포트에만 연결한다. proxy/redirect를 사용하지 않고 요청별60초·응답2MiB 한도와 호출자 guard를 적용한다. health/model alias 확인 뒤 고정6개+tokenizer edge4개의 토큰/문자열/streaming 결과, 원본 token ID/decode,finite logprob·usage·SSE DONE/마지막usage/종료순서를 대조한다. 4095+1 경계,4096+1 거부와 EOS만 허용하는 별도 진단을 합쳐34개 completion 요청이다. 일반 생성은 ignore_eos=False이며 강제EOS 진단을 자연 생성 품질의 증거로 사용하지 않는다.

실제 localhost 모의 HTTP 서버·chunked SSE·UTF-8 분할·변조/잘린응답/시간 및 크기 제한 등31개 검사가 통과했다. `scripts/check_serving_protocol.py`는 격리 vLLM0.11.2의 실제 프로토콜/샘플링 클래스로34개 요청과 합성 응답 직렬화를 확인했다. 근거 `runs/main_training_35k_plan_v1/serving_http_preparation_v1.json`. GPU·실제 vLLM 서버·최종3B 추론은 실행하지 않았다.

이 도구만으로 포트의 서버 소유권이나 최종 가중치를 인증하지 않는다. 이 연결은 `scripts/run_final_serving.py`와 `inference/vllm_server.py`, `inference/serving_process.py`에 구현했다. 정확한 결과 경로는 `artifacts/3b_base_35k_v1/vllm_serving_v1/`이고 관리기는 이미 대기 중이다. 중복 기동하지 않는다.

오프라인 vLLM 완료를 fresh source/실행 증명으로 재검토한 뒤 공용GPU 잠금과 실제 비점유·자원 준입을 확인한다. 서버는 원HF 전체SHA를 검사하고, SO_REUSEPORT를 쓰지 않는 전용127.0.0.1 소켓을 먼저 확보해 고정 vLLM의 run_server_worker에 전달한다. 실제PID/start_ticks/owner/명령/fd/inode와 /proc TCP LISTEN을 대조한 뒤34개HTTP요청을 보낸다. 주 감독 루프는 HTTP와 독립적으로 합산PSS·시간·공간·GPU소유자를 확인하며 완료 후 자체서버에 pidfd SIGTERM을 전달하고 모든 소유 프로세스의 종료를 확인한다. 실패 증거는 보존하며 같은 경로를 자동 재시도하지 않는다.

CPU의 실제 소유서버 기동→HTTP→종료/잘못된응답/자원실패 정리, 소켓대체/포트공유 거부, 합성GPU·최종소스 receipt의독립검토/10개변조거부 등 총39시험이 통과했다. 격리vLLM0.11.2의 실제 CLI/34요청 프로토콜도 CUDA비노출에서 확인했다. **실제3B GPU서버 검증은 여전히 대기**다. 관리기준비 근거 `runs/main_training_35k_plan_v1/serving_controller_preparation_v1.json`.

CPU8~15/thread4,H200한장,bf16/eager/context4096/메모리비율0.25,전체1800초/초기화900초,서버+감독 합산PSS64GiB를 사용한다. API증거1GiB는기존export100GiB 안,격리환경+cache64GiB는공유scratch1024GiB 안이며오프라인과GPU동시실행하지않는다. API성공 시 `report.json`은 `http_checks.json`, `server_execution.json`, `server/result.json`, `gpu_samples.json`, `resource_peak.json`과출처/manifest에연결된다. 지연/처리량 계산은 요청 전체시간(prefill+HTTP 포함) 기준이며 실제 VRAM과 운영 처리량은 향후 서버 실행 때 측정한다. 기존 오프라인 관리기와 구별하며 API 완료·최적화 성공으로 표시하지 않는다.

## Ollama/GGUF: 확인한 버전의 토크나이저 제한

2026-11-08 조회한 Ollama 안정 릴리스는 `v0.40.15`, commit `02480ca2a47153e1f9c31cbe1e05536da97ce1ba`였다. 원본 tokenizer의 전처리는 `Digits(individual_digits=True)` 다음 `ByteLevel`이며 정규화/post-processor는 없다.

- llama.cpp commit `7d7f0287530bc8981690f361d8c1627dbae1c99a`의 변경하지 않은 `get_vocab_base_pre()` 함수에 실제 토크나이저를 입력해 실행했다. `BPE pre-tokenizer was not recognized`로 거부됐다. 판정 지문은 `2ecb0689dbb27a427965e2ef401a87c09bdb843cad2230e9ca341a93aa1204dc`이다. 원본/HF 포장본의 ID는 일치했다. 이는 실제 변환기의 **토크나이저 인식 단계** 실행이며 전체 GGUF 변환·모델 실행은 아니다.
- Ollama v0.40.15의 `mlxrunner/tokenizer/tokenizer_pretokenizer.go`는 검토한 main commit과 같은 Git blob이다. 소스의 지원 분기는 Sequence/Split/ByteLevel이며 Digits는 오류를 반환하는 default로 들어간다. 이것은 고정 소스 검토 결과이고 Go 파서나 Ollama 바이너리를 실행한 결과가 아니다. Llama architecture 등록만으로 이 tokenizer의 지원이 성립하지 않는다.
- 이전 ByteLevel 단독 대체 시험에서는 `  123`과 CRLF 뒤 숫자의 ID가 원본과 달랐다. 이 대체 시험도 실제 Ollama 실행으로 취급하지 않는다. 이름/지문을 억지로 등록하거나 Digits를 제거하는 방식은 채택하지 않는다.

따라서 현재 확인한 경로에서는 Ollama 호환 완료로 표시할 수 없다. 최종 사용 패키지는 우선 원본 tokenizer를 유지하는 HF/vLLM의 실제 검증을 완료한다. 향후 Ollama/GGUF 지원 구현을 검토하려면 원본과의 token ID/decode·특수토큰·수치·실제 추론 동등성을 새로 입증해야 한다. 원본 학습/토크나이저 변경이나 지원 패치는 자동 적용하지 않는다.

근거: `runs/main_training_35k_plan_v1/ollama_compatibility_v2/report.json`, `source_manifest*.json`, `release_comparison.json`. 원격 소스9개291,596bytes의 Git blob SHA를 대조했다. 전체 binary/모델을 내려받거나 GPU를 사용하지 않았다.

공식 자료: [Ollama 모델 가져오기](https://docs.ollama.com/import), [Ollama v0.40.15 전처리 구현](https://github.com/ollama/ollama/blob/02480ca2a47153e1f9c31cbe1e05536da97ce1ba/mlxrunner/tokenizer/tokenizer_pretokenizer.go), [고정 llama.cpp 변환기](https://github.com/ggml-org/llama.cpp/blob/7d7f0287530bc8981690f361d8c1627dbae1c99a/conversion/base.py). 외부 업로드·SFT·공식 벤치마크는 이 문서의 자동 실행 범위가 아니다.

## 사용자 문서 초안

`runs/main_training_35k_plan_v1/usage_draft_v1/`에 MODEL_CARD.draft.md, USAGE.draft.md, completion.json/streaming.json, commands.json, requirements.lock과출처SHA를준비했다. 사용자가별도로기동할foreground localhost:8000 후보명령이며현재학습중에는실행하지않는다. 실제고정vLLM CLI/요청스키마는CPU에서확인했고최종3B 사용자명령실행은미검증이다.

모델카드는shape합산3.22B·28층/3072/24head/8KV·원65536tokenizer,35k기대투입량과실제ledger대기,Base모델/1B품질위험·최종3B미측정·Ollama제한을구분한다. 최종검증후별도use 디렉터리에확정문서와실제결과를담으며HF sealed파일목록에는문서를추가하지않는다. 근거 `runs/main_training_35k_plan_v1/usage_draft_validation_v1.json`.
