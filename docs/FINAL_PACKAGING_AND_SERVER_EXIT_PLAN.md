# 최신 실행 범위와 후처리 인계

2026-11-16 사용자 Goal은47,000 조기 종료·native 평가·일반 및 부모 독립8GPU 복원·보존 감사·영구 NFS 인계다. 현재8GPU 서버 종료 조건은 `GOAL_8GPU_TRAINING_HANDOFF.md`, 다른 H2001GPU 기기의 BF16·Q8_0·Q4_K_M 가공과 vLLM/Ollama 실제 검증 조건은 `GOAL_1GPU_POSTPROCESSING_47000.md`를 따른다. 실제 최종 checkpoint와 증거는47K 인계 manifest로 확정한다. 아래50K 경로와 기존 관리기 설명은 과거 준비 근거로 보존한다.

# 50K 학습 완료 후 패키징·플랫폼 검증·서버 작업 종료 계획

작성 목적: 사용자 요청에 따라 최종 모델을 vLLM과 Ollama에 실제 탑재·시험하고 이 서버의 프로젝트 작업을 종료한다. 50,000step 목표와 기존 필수 저장·복원·native 평가 조건은 유지한다. 현재 문서는 실행 계획이며 기존 고정 producer/후속관리기 계약을 변경하지 않는다.

## 확인된 준비 상태
- vLLM 0.11.2 격리 환경의 CPU import 기록이 있다(torch2.9.0+cu128, transformers4.57.6). 근거: runs/main_training_35k_plan_v1/serving_environment_v2/import_report.json 및 runs/main_training_50k_plan_v1/vllm_environment_precheck_v1.json. 재사용 전 실제 환경 존재·버전·코드 계약을 재확인한다. 최종50K GPU 추론은 미검증이다.
- Ollama0.40.16을 기존 scratch의 ollama_v04016_v1/install에 격리 설치하고 SHA/CLI version/help exit0을 검증했다(installation_v1.json). PATH·학습 환경은 변경하지 않았다. localhost 빈 모델 API 버전/목록 응답과 정상 종료도 검증했다(local_api_v1.json). 작은 임의가중치 진단모델의 실제 import/CPU생성/재로딩과 HF greedy 출력 일치도 확인했다(tiny_ollama_api_v1.json). 최종50K3B import·생성은 미검증이다.
- runs/main_training_35k_plan_v1/ollama_compatibility_v2/report.json의 고정 llama.cpp 인식 함수를 실제 실행했을 때 BPE pre-tokenizer 미인식 오류가 발생했다. 이후 정확한 tokenizer SHA에 한정한 별도 후보등록 경로로 진단용 GGUF 변환·설치backend108건 ID대조·작은모델 Ollama 실행을 확인했다. 원 upstream 자동인식과 최종3B 변환/실행 성공은 여전히 별도다. 소스 일부의 MLX 검사 결과를 이 Linux/CUDA 경로의 실패 증명으로 확대하지 않는다.
- 기존 최종후속관리기에는 native 평가·복원·HF/vLLM 단계가 준비되어 있다. 새 실행기는 기존 관리기와 중복 GPU 작업을 시작하지 않도록 소유권·완료 증거를 연결해야 한다.

## 단계와 완료 조건
| 순서 | 시점 | 수행 | 완료 증거 |
|---|---|---|---|
| 1 | 학습 중 | 격리 vLLM 환경 재확인, Ollama 버전 고정·별도 바이너리/모델 캐시 설치, llama.cpp 변환 도구 고정 | 경로·버전·다운로드 SHA·실행 도움말/CPU 검사 기록 |
| 2 | 학습 중 | 실제 최종 tokenizer로 Linux 대상 변환/토큰 ID 호환성 사전 검사; 한영·숫자·코드·공백·Unicode·BOS/EOS 확인 | HF/native 토큰 ID 대조, 변환 지원 판정. 미지원이면 정확한 원인·수정 범위·대안 보고 |
| 3 | 50K 완료 | 실제8rank/목표104,857,600,000tokens 확인, 최종 NFS 전체 상태 SHA·인증, 기존 필수 native 평가·별도 실제8GPU 복원 | 원본 목표step 불변, 진단step 별도 기록, 재개·평가 보고서 |
| 4 | 최종 인증 후 | native→HF safetensors/config/tokenizer 패키지, 별도 LM head·QKV·RoPE·norm/vocab 검사 | native/HF logits·NLL·cache·greedy 및 토큰 ID 검증 |
| 5 | GPU 반납 후 | HF 패키지를 vLLM에서 로딩, offline 및 localhost completions API 시험 | 실제 한영·코드·수학 입력 출력, 유한값·길이·종료·반복 요청, 생성속도/VRAM, 종료 후 재로딩 성공 |
| 6 | vLLM 시험 후 | 지원 확인된 경로로 GGUF 또는 지원 safetensors import, Modelfile 발행, Ollama 등록·실제 generate API 시험 | 원 tokenizer 의미 보존, 실제 생성·재로딩·로그 기록. 무양자화 먼저, Q4_K_M은 선택 추가 |
| 7 | 양쪽 검증 후 | 최종 패키지·재개 체크포인트·코드/버전/입력계약·실행법·SHA·증거를 영구 보존하고 보존 감사 | NFS 최신3개 조건 및 보호사본 처리 증거, 다른 기기 재현 가능한 인계 묶음 |
| 8 | 보존 검증 후 | 이번 프로젝트 소유 테스트 서버/후속관리기/감시 작업 정상 종료, 잔존 PID·GPU 점유·미완료 쓰기 확인 | 종료 보고서, 최종 파일 목록/크기/SHA/위치, 실패·미검증 항목 명시 |

## Ollama 변환 입력의 실제 발행 증명
- 기존 HF 관리기는 최종 checkpoint·8rank 장부·native평가·8GPU복원·보존 감사를 결합한 source attestation과 `hf/release_manifest.json`, `hf/numerical_validation.json`, `worker_result.json`을 발행한다. Ollama 작업 전 `export_budget_hf.load_contract`와 `verify_result`를 호출하고 별도로 완료 marker·보고서를 대조해 `verify_result`/`verify_published`의 source·payload 재검증뿐 아니라 `.done`의 계약·보고서 SHA와 보고서 일치까지 확인한다. 완료마커가 검사 중 사라지면 controller가 기동 분기로 들어갈 수 있으므로 Ollama 전용 읽기검사에서는 controller를 호출하지 않는다. 임시 `hf.copying`, 파일 존재, 작은 진단 성공만으로 변환하지 않는다.
- Ollama 발행 구현은 아직 없다. 새 변환기의 정확한 source binding, tokenizer SHA, upstream 코드/라이브러리 버전, 출력 임시경로, 크기/공간 제한, 내용검증·원본동일성·재시작/실패잔여물·최종발행을 구현하고 검증한다. 검증 전 기존 진단 wrapper의 크기제한을 제거하거나 본학습 고정 관리자에 연결하지 않는다.
- HF stage와 publication은 하드링크를 사용하므로 공간 합산 때 inode와 파일시스템을 실측해 중복계상하지 않는다. 별도 GGUF·Ollama blob은 실제 별도 점유 및 변환 중 peak를 확인한다.

## 설치·실행 원칙
- 학습 torch/CUDA/고정코드 변경 없이 격리 설치한다. 설치 준비는 CPU/디스크 여유와 후속 peak를 재측정하고 수행한다. 학습 중 GPU 모델 로딩이나 상시 서비스 기동은 하지 않는다. 설치 확인용 CPU/localhost 단기 API 시험은 모델을 로딩하지 않고 소유 프로세스·포트 종료까지 검증한다.
- 서비스는 localhost에만 바인딩하고 프로젝트 전용 포트·모델 캐시를 사용한다. 시스템 전체 서비스나 다른 작업을 종료하지 않는다.
- 두 추론 엔진은 순차 실행한다. 3.22B bf16 가중치 순수 크기는 약6.44GB(약6GiB)이며 실제 패키지·복사본·Ollama blob 캐시·환경·KV cache는 별도 실측한다. 이 수치를 실제 전체 저장량으로 사용하지 않는다.
- 기존 로컬 임시1024GiB 풀·저장소별200GiB 안전여유를 지키며 환경/변환/등록 사본을 포함한 peak를 계산한다. 이미 점유된 환경은 다시 예산에 더하지 않는다. 공간 부족 시 임의 다른 자료 삭제 없이 보고한다.
- 기존 관리자에 고정된 파일을 덮어쓰지 않는다. 후속 연결이 필요하면 별도 경로/계약으로 구현·검증하고 실제 실행 소유권을 확인한다.
- Ollama 미지원이면 tokenizer를 유사 설정으로 바꿔 성공 처리하지 않는다. 변환기 또는 런타임 지원 보완에 필요한 시간·재현성·배포물 범위를 보고한다. 사용자의 판단 없이 두 플랫폼 성공 조건을 한 플랫폼으로 축소하지 않는다.
- Base 모델의 이어쓰기 동작 시험을 기준으로 한다. 임의 chat template나 SFT 완료를 가정하지 않는다. 정상 로딩/응답과 답변 품질은 별도로 평가한다. SFT·공식 벤치마크·장기 서비스 운영은 이번 범위에 추가하지 않는다.

## 최종 보존과 종료 경계
- 기존 고정 후속관리기의 HF 발행 경로는 `artifacts/3b_base_50k_v1/hf`이며 `hf.copying`에서 수치 검증 후 발행한다. vLLM 결과도 같은 상위 경로의 `vllm_runtime_v1` 및 `vllm_serving_v1`을 사용한다. Ollama 및 인계 산출물은 별도 신규 경로를 준비하고 기존 100GiB 풀과 경합·중복 예산 여부를 실행 전 검증한다. Ollama 지원 실패 시 ollama 디렉터리를 성공 패키지로 발행하지 않는다.
- 모델 패키지는 추론용이며 optimizer/8rank RNG 등이 포함된 완전 재개 체크포인트를 대체하지 않는다. 최종 전체 재개 상태와 코드·tokenizer·mix/데이터 계약·재현법을 별도 보존한다.
- 로컬에만 있는 산출물은 서버 종료 시 유실될 수 있다. 사용자가 서버 반납 후에도 NFS가 유지됨을 확인했다. 최종 산출물과 필수 재현/재개 파일을 NFS에 보존하고 로컬 전용 의존성 부재·전체 SHA·인계 경로를 확인한다. 이 보존을 위한 외부 업로드는 필요하지 않다. 새 서버에서 사용할 마운트/접근 안내를 인계 문서에 기록하며 비밀값은 기록하지 않는다.
- 이번 계획의 종료는 프로젝트 작업 프로세스 종료다. 서버 전원 종료·인스턴스 반납·NFS 삭제는 자동 실행하지 않는다.
- vLLM/Ollama 양쪽 실제 최종 모델 성공 또는 사용자 승인된 범위 변경, 기존 필수 최종 검증, 안전한 보존·인계가 충족돼야 완료 처리한다.

## 다음 작업과 현재 제한
다음 작업은 격리 환경 재확인 및 Ollama 설치/실제 tokenizer 호환성 사전 점검이다. 이번 계획 작성에서는 설치·GPU 시험·업로드·삭제·학습 변경을 실행하지 않았다. 기존 관측기 메모리 예외의 대체 연결 승인 대기는 별도이며 이 계획으로 자동 해소하지 않는다.
REF-EXPORT X01, REF-TOK T01/T02, REF-EVAL E01~E03을 변환·동일성·생성 검증에 적용한다. 작은 fixture/소스 검토를 실제 최종3B 성공으로 표시하지 않는다.

공식 참고: https://github.com/ollama/ollama/blob/main/docs/import.mdx 및 https://github.com/vllm-project/vllm/blob/main/docs/serving/online_serving/openai_compatible_server.md . 실행 때 고정한 버전 문서와 실제 동작을 함께 확인한다.
