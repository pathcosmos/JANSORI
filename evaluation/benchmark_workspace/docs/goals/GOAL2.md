# GOAL 2 상세 사양 — Opus 5.5 / effort high
# 최적 설정 탐색·적용 → 시중 모델과 비교 벤치 → 벤치 수트 업그레이드 → 보고

## 시작 조건
- Goal 1이 끝나 있어야 한다. 다음 파일이 있는지 먼저 확인한다:
  configs/standard/formats.yaml, kobench/standard/evafrill_engine.py, runs/evafrill_engine_v1/REPORT.md
  없으면 생성 평가와 성능 측정에서 EVAFRILL을 빼고 진행하며, 그 사실을 기록한다.
- 작업 디렉터리: /home/work/llm-dense/llm-test. 먼저 CLAUDE.md를 읽는다.
- 모든 셸에서 먼저: source env/paths.sh. 실행은 $LT_VENV/bin/python.
- 벤치 수트: ko-llm-bench-suite/ (브랜치 feat/standard-benchmarks, 커밋 안 함)
  - kobench/standard/{run,report,registry,evafrill_lm}.py
  - configs/standard/{models,suites}.yaml
- H200 1장, CPU 12, RAM 약 240 GiB.
  이미 갖춘 것: vLLM 0.33, lm-eval 0.4.13, CUDA 확장, llama.cpp CUDA, Ollama.
- 공개 비교 모델 30종은 모두 revision이 고정되어 있고 다운로드되어 있다 (Llama-3.2 2종 포함).
  gated 데이터셋 KMMLU-Redux와 KMMLU-Pro도 접근 승인이 끝났다.
- 이미 진행된 것: JANSORI와 frankenstallm의 ko_mc_v1(A 표) 실행.
  로그는 runs/standard_v1/. 끝나지 않았으면 이어서 실행한다 (완료된 태스크는 자동으로 건너뛴다).

## 대상 모델
- 사용자 모델
  - JANSORI: base. BF16, Q8_0, Q4_K_M
  - frankenstallm: instruct. BF16 + GGUF v2
  - EVAFRILL-Mo-3B: 최종(slerp), pretrain, dev로 선택한 단계
- 1순위 비교군 (반드시 포함)
  - dense base: llama-3.2-3b, qwen2.5-3b, kanana-1.5-2.1b-base, kanana-2-3b-base, qwen3-4b-base, smollm3-3b-base
  - dense instruct: llama-3.2-3b-instruct, qwen2.5-3b-instruct, kanana-1.5-2.1b, kanana-2-3b, exaone-3.5-2.4b,
    qwen3-4b-2507, smollm3-3b
  - A3B: qwen3-30b-a3b-base / qwen3-30b-a3b-2507, kanana-2-30b-a3b-base / kanana-2-30b-a3b,
    nemotron-3-nano-30b-a3b-base / nemotron-3-nano-30b-a3b
- 2순위 (1순위를 끝낸 뒤)
  - qwen3.5-4b-base / qwen3.5-4b, gemma-4-e4b / gemma-4-e4b-it (총 8B임을 표시), phi-4-mini,
    qwen3.5-35b-a3b-base / qwen3.5-35b-a3b, qwen3.6-35b-a3b, nemotron-3.5-lightning-30b-a3b,
    ernie-4.5-21b-a3b-base / ernie-4.5-21b-a3b
- 모든 표에 총/활성 파라미터를 표시한다. base와 instruct는 다른 표로 비교한다.

## 1단계: 최적 설정 탐색과 적용 (dev/validation에서만)
- Goal 1의 formats.yaml(학습 형식)을 적용한다.
- 다음을 dev에서 비교하고 채택한다.
  - 로그우도 채점에서 문서 시작 토큰(EOS)을 붙인 경우와 붙이지 않은 경우
  - few-shot 수
  - 생성 디코딩: greedy와 모델 카드 권장값 (temperature, repetition penalty, 종료 조건, 최대 길이)
  - EVAFRILL 체크포인트 (태스크 유형별)
- 공개 비교 모델에도 각자의 공식 chat 템플릿과 권장 설정을 같은 절차로 적용한다.
- 기존 방식 대비 효과를 dev와 test 모두에서 CI와 함께 기록한다. 단, 선택에는 dev 결과만 쓴다.
- 산출물: configs/standard/best_settings.yaml (모델별 설정, 근거가 된 dev 점수, 날짜)

## 2단계: 벤치 수트 업그레이드
- 결과는 두 가지로 보고한다.
  - A 표, 공통 프로토콜: 모든 모델에 같은 프롬프트, 같은 shot 수, greedy 디코딩
  - B 표, 모델별 최적 설정: best_settings.yaml을 적용
- 표준 객관식(로그우도)
  - ko_mc_v1: KoBEST 5종, KMMLU, HAE-RAE, CLIcK, Belebele-ko, Global-MMLU-ko
  - en_mc_v1: MMLU, ARC-C, HellaSwag, Winogrande
  - 추가: KoBALT-700
  - 추가: KMMLU-Redux(2,587문항), KMMLU-Pro(2,822문항)
    - lm-eval 0.4.13에 태스크가 없고 test 분할만 있으므로, 0-shot 커스텀 lm-eval 태스크(YAML + utils)로 만든다.
    - 선택지 수가 문항마다 다를 수 있으니 확인해서 처리한다.
    - 데이터셋 revision을 고정하고, 비상업 라이선스라는 점을 결과에 표시한다.
- GGUF(Q8/Q4)의 로그우도와 PPL은 llama.cpp(llama-server, llama-perplexity)로 측정해 BF16과 비교한다.
  Ollama는 프롬프트 logprob을 주지 않는다.
- PPL: 한국어와 영어의 bits-per-byte. 중립 코퍼스를 쓰고, JANSORI holdout은 학습 분포와 같다는 점을 표시해 따로 둔다.
- 생성(instruct): HRM8K / GSM8K-ko, IFEval-Ko, KoMT-Bench, LogicKor, ko-arena-hard, K2-Eval,
  KoSimpleQA, KorMedMCQA, KoBBQ, ko_truthful_qa.
  - 정답이 있는 문항은 규칙으로 채점한다. 형식 실패와 오답은 따로 집계한다.
  - thinking 모드가 있는 모델은 끈 상태와 켠 상태를 구분한다.
- Judge: 로컬 Qwen3.6-35B-A3B(또는 그와 같은 수준)를 vLLM에서 동시 요청을 많이 걸어 쓴다.
  - 위치를 바꿔 두 번 비교한다.
  - judge와 같은 계열의 모델을 평가할 때는 그 사실을 표시한다.
- 코드: HumanEval+/MBPP+를 자원·파일·네트워크를 제한한 격리 실행으로 채점한다.
- 성능
  - 클라이언트 측 TTFT, TPOT, 처리량. 동시 요청 1–64, prefix caching, VRAM 최고치.
  - 서버 기동과 예열은 분리한다.
  - dense와 A3B를 같은 표에 넣는다. EVAFRILL은 Goal 1의 엔진으로 측정한다.
- 기존 트랙(T2–T7)의 Ollama 전용 경로를 백엔드 추상화(vLLM / llama.cpp / EVAFRILL 엔진)로 옮긴다.
  자체 작성 문항은 custom으로 표시한다.
- 통계: 문항별 결과를 저장한다. bootstrap 95% CI와 사용자 모델 대비 paired 차이를 계산한다.
  데이터셋·모델 revision, 설정, 버전을 기록한다. 새 기능에는 단위 테스트를 붙인다.

## 3단계: 비교 실행과 보고
- 실행 순서: 사용자 모델 → 1순위 → 2순위.
- 공개 벤치마크는 학습 데이터 오염 가능성이 있다는 점을 해석에 명시한다.
- 최종 보고서: 한국어, 표와 차트.
  - A·B 표
  - 각 모델의 강점과 약점
  - 최적 설정의 효과 (향상분과 CI)
  - 양자화에 따른 손실
  - 성능(속도·메모리)
  - 실패하거나 확인하지 못한 항목
  - 공유용 페이지로 게시하고 링크를 남긴다 (외부 공개 범위는 사용자에게 확인한다).

## 규칙
- test 결과를 보고 설정을 고르거나, 문항에 맞춰 프롬프트를 조정하지 않는다.
- 결과는 사실대로 보고한다. 실패 로그는 보존하고, 재시도는 새 로그와 새 run에 남긴다.
  장시간 작업은 중단 후 재개할 수 있게 만든다.
- GPU 작업은 한 번에 하나만. 다운로드와 빌드는 CPU 쪽에서 병행한다. OOM은 /sys/fs/cgroup/memory.events로 확인한다.
- 새 패키지는 공개된 지 2주 이상 지난 버전만 쓴다 (uv --exclude-newer). 재구성은 env/setup.sh만으로 가능해야 한다.
- 사용자 확인이 있어야만 하는 일: git commit/push, HF 업로드나 모델 카드 수정,
  ../llm-traditional 수정·삭제, 외부 게시.
- 진행 상황과 결정은 CLAUDE.md에 계속 반영한다.

## 완료 기준
1. best_settings.yaml이 있고, 사용자 모델의 최적 설정 효과가 수치(CI 포함)로 제시되었다.
2. 1순위 비교군 전체에 대해 표준 MC(KMMLU-Redux/Pro 포함), PPL, 생성, 성능 벤치가 끝나고 A·B 표가 생성되었다.
   2순위는 실행한 범위를 명시했다.
3. 벤치 수트에 새 기능과 테스트가 반영되었고 전체 테스트가 통과한다.
4. 한국어 최종 보고서가 게시되었고, 재현 명령과 남은 과제가 정리되어 있다.
