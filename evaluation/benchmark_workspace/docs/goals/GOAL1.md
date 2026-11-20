# GOAL 1 상세 사양 — Opus 5.5 / effort xhigh
# 사용자 모델 실행 정합성 검증 + EVAFRILL 증분 디코딩 엔진

## 목적
이후 모든 평가의 토대가 되는 두 가지를 정확하게 만든다.
(A) 사용자 모델 3종의 프롬프트·토큰화 형식이 실제 학습 파이프라인과 바이트 단위로 일치함을 검증하고 고정한다.
(B) EVAFRILL-Mo-3B가 원본과 같은 결과를 내면서 빠르게 생성하도록 증분 디코딩 엔진을 만들고 검증한다.
이 Goal에서는 벤치마크 점수를 내거나 비교하지 않는다 (Goal 2의 일이다).

## 작업 위치와 먼저 읽을 것
- 작업 디렉터리: /home/work/llm-dense/llm-test. 먼저 CLAUDE.md를 읽는다.
- 모든 셸에서 먼저: source env/paths.sh. 실행은 $LT_VENV/bin/python.
- 벤치 수트: ko-llm-bench-suite/ (브랜치 feat/standard-benchmarks, 커밋 안 함). kobench/standard/에 표준 MC 모듈이 있다.
- H200 1장. GPU 작업은 한 번에 하나만 실행한다.
  시작할 때 nvidia-smi로 다른 GPU 작업(ko_mc_v1 등)이 없는지 확인하고, 있으면 끝날 때까지 CPU 작업을 먼저 한다.
- 이미 갖춘 것: mamba-ssm 2.3.2.post1과 causal-conv1d 1.7.0 CUDA 확장(sm_90),
  vLLM 내장 Mamba Triton op, llama.cpp CUDA.
- 모델 경로
  - EVAFRILL: $HF_HOME/hub/models--pathcosmos--EVAFRILL-Mo-3B/snapshots/e95edece955af9df9a6215210db594a9984fddf5
    체크포인트는 루트(최종 slerp)와 pretrain, sft-v2, dpo-r1, dpo-r2, dpo-r3, orpo, slerp 하위 폴더
  - EVAFRILL 원본 소스: $LT_SRC/EVAFRILL-Mo (commit은 env/evafrill-commit.txt)
  - frankenstallm: $HF_HOME/hub/models--pathcosmos--frankenstallm/snapshots/b9f7777f01479b18e5a3a55f9cad4582df122f7d
    학습 코드 data/, source/가 함께 있다
  - JANSORI: ../llm-traditional/runs/3b_47k_hub_release_v1/nfs-release-v1/{bf16,gguf}
    학습 코드는 ../llm-traditional (읽기만 한다)

## A. 학습 형식 정합성 (3종)
1. 학습 코드에서 다음을 확인하고 근거 파일과 줄 번호를 기록한다.
   - 사전학습의 문서 구분 방식
   - BOS/EOS 사용
   - SFT/ORPO/DPO 템플릿
   - 시스템 프롬프트 사용 여부
   - 손실을 계산한 구간
   이미 확인된 것:
   - JANSORI와 EVAFRILL 사전학습은 ids+[eos]이고 BOS가 없다
   - frankenstallm과 EVAFRILL SFT 템플릿은 "<|user|>\n{질문}\n<|assistant|>\n{답}</s>"
   멀티턴, 시스템 메시지, 공백·개행 처리는 아직 확인하지 않았다.
2. 학습 코드의 토큰화 함수로 만든 토큰 ID와, 평가 경로가 만드는 토큰 ID가 같은 입력에서 정확히 일치함을 검증한다.
   평가 경로는 vLLM chat 템플릿, lm-eval 로그우도 인코딩, llama.cpp/Ollama 템플릿이다.
   입력은 한국어, 영어, 공백, 개행, 특수문자 조합 20개 이상으로 한다.
3. 산출물
   - configs/standard/formats.yaml: 모델별 템플릿, 시작 토큰, 종료 토큰, 근거
   - 로컬 chat 템플릿 덮어쓰기 파일 (HF 원본은 수정하지 않는다)
   - 수정한 Ollama Modelfile
   - 토큰 일치 테스트 (pytest)
4. 확인된 것만 보고한다: 기존 수트(Modelfile TEMPLATE {{ .Prompt }}, evafrill_runner "{system}\n{prompt}")가
   학습 형식과 어떻게 달랐는지 실제 토큰 차이를 예시로 남긴다.

## B. EVAFRILL 증분 디코딩 엔진
- 원본 model/transformer.py의 forward에는 캐시가 없다. 원본 소스는 수정하지 않고 래퍼 모듈로 구현한다
  (예: kobench/standard/evafrill_engine.py).
- 블록 구조 (표준 Mamba-2와 다르다)
  - in_proj → (z, x, B, C, dt). conv1d는 x에만 적용하고 SiLU를 건다.
  - SSD 출력은 y·silu(z)로 게이트한다 (RMSNormGated 없음). D 스킵, dt_bias, softplus.
  - 그다음 out_proj → 잔차 → (ffn_norm → SwiGLU) → 잔차.
  - A 블록 2개(인덱스 12, 24)는 GQA 24/8, RoPE θ=500000.
- prefill: 원본과 같은 커널로 계산하고 마지막 상태를 반환한다.
  - mamba_chunk_scan_combined(return_final_states)
  - causal_conv1d의 마지막 conv 입력 (K−1개)
  - 어텐션 KV
- decode: causal_conv1d_update, selective_state_update(z, D, dt_bias, dt_softplus), KV 캐시 어텐션.
  배치(패딩·길이가 다른 프롬프트)를 지원한다. 정적 배치 decode 단계는 CUDA graph로 캡처한다.
- 검증 (모두 통과해야 채택한다. 허용 오차는 측정 전에 정하고 기록한다)
  1. 체크포인트 최소 3개(pretrain, sft-v2, slerp)에서 길이 1·17·512·4000 프롬프트의
     prefill 로짓이 원본 전체 forward와 일치한다.
  2. 이후 256토큰 decode의 매 단계 로짓이 "같은 시퀀스로 원본 전체 forward를 다시 계산한 값"과 일치한다
     (BF16 기준 허용 오차 + FP32 기준선 비교).
  3. greedy 생성 토큰열이 원본 방식(매 단계 전체 forward)과 6개 이상 프롬프트에서 같다.
     다르면 처음 달라지는 위치와 원인(로짓 차이가 거의 동률인지)을 분석한다.
  4. 배치 1, 8, 32에서 결과가 배치 1과 같다 (패딩 위치가 결과에 영향을 주지 않는다).
  5. 실패한 기록은 보존한다. 허용 오차를 사후에 바꾸지 않는다. 바꿔야 한다면 사용자에게 근거를 묻는다.
- 성능 측정: 원본(캐시 없음) 대비 엔진의 TTFT, decode tok/s, 배치 처리량, VRAM.
  입력 128/1024/3968, 출력 128/512, 배치 1/8/32. CUDA graph를 켠 경우와 끈 경우를 비교한다.
- lm-eval 연결: EvafrillLM.generate_until을 엔진으로 구현한다 (종료 문자열·EOS·max_gen_toks 처리).
  생성 결과가 엔진 단독 실행과 같음을 테스트한다.
- 산출물: 엔진 모듈과 단위 테스트, 검증 결과 runs/evafrill_engine_v1/(verification.json, perf.csv, REPORT.md).

## 규칙
- 결과는 사실대로 보고한다. 미검증 항목과 실패도 숨기지 않는다. 실패 로그는 보존하고 재시도는 새 run에 남긴다.
- 새 패키지는 공개된 지 2주 이상 지난 버전만 쓴다 (uv --exclude-newer). 환경을 바꾸면 env/setup.sh에 반영한다.
- 기존 테스트(현재 535개)는 계속 통과해야 한다.
- 사용자 확인이 있어야만 하는 일: git commit/push, HF 업로드, ../llm-traditional 수정·삭제, 외부 게시.
- 끝나면 CLAUDE.md에 결과, 사용법, 허용 오차, 남은 한계를 기록한다. Goal 2가 그것을 읽고 이어 간다.

## 완료 기준
1. configs/standard/formats.yaml과 토큰 일치 테스트가 있고, 3종 모두 학습 형식과 일치함이 검증되었다.
2. EVAFRILL 엔진이 검증 1–4를 통과했고, 그 결과가 runs/evafrill_engine_v1/REPORT.md에 있다.
3. EvafrillLM이 생성을 지원하고, 원본 대비 속도 향상이 수치로 기록되었다.
4. 전체 테스트가 통과하고 CLAUDE.md가 갱신되었다.

## 변경 기록
- 2026-11-18: 검증 3(V3)의 판정 기준을 사용자 승인으로 변경했다 — "greedy 토큰열 6/8 이상 동일" → "교사 강제 결정 일치(원본 margin ≥0.5인 모든 단계) + 자유 생성 분기는 모두 margin <0.5". 근거와 원래 결과: runs/evafrill_engine_v1/tolerance_amendment_v3.json, REPORT.md.
- 2026-11-18T20:58:38+09:00: 사용자 결정 — Goal 1을 승인된 V3 변경 기준으로 **완료 처리**한다. Goal 원래 문구의 V3(greedy 6/8 동일)는 최종 엔진에서 pretrain 1/8, sft-v2 5/8, slerp 6/8로 불충족이었고, 이 사실은 runs/evafrill_engine_v1/REPORT.md에 기록되어 있다. 나머지 완료 기준(형식 일치 전 경로, V1·V2·V4 엄격, 성능, 테스트 554개 통과, CLAUDE.md)은 원래 문구대로 충족했다.
