# JANSORI 평가 기록 (evaluation/)

[한국어](#한국어) · [English](#english)

## 한국어

JANSORI(47,000스텝 공개 체크포인트, [pathcosmos/JANSORI](https://huggingface.co/pathcosmos/JANSORI))를 공개한 뒤 수행한 배포 검증·상세 평가·표준 벤치 비교 기록이다.
평가 코드는 범용 수트 [pathcosmos/ko-llm-bench-suite](https://github.com/pathcosmos/ko-llm-bench-suite/tree/4674cd4c593fbd4d970493eecda5e899b23dfe9b)(브랜치 `feat/standard-benchmarks`, commit `4674cd4c593fbd4d970493eecda5e899b23dfe9b`)에 있고,
이 폴더에는 그 수트로 실행한 설정·스크립트·로그·결과 표와 학습 저장소 쪽 배포·평가 기록을 모았다.

### 구성

| 경로 | 내용 |
|---|---|
| `jansori_release_v1/` | HF 배포 검증: 18개 파일 SHA, 빈 캐시 다운로드, vLLM·Ollama 세 형식(BF16·Q8_0·Q4_K_M) 생성·재로딩 확인, 시험·실행 설정 문서 |
| `jansori_detailed_eval_v1/` | 형식별 상세 평가: 생성 98입력, 코드 함수 12조건, KoBEST BoolQ test 1,404문항, holdout PPL bootstrap, H200 단독 성능 27조건 |
| `benchmark_workspace/` | 공개 모델 비교 작업 공간: 환경 재구성(`env/setup.sh`, 버전 lock), 목표 사양(`docs/goals/`), 실행 스크립트·로그(`runs/`), 작업 기록(`CLAUDE.md`) |
| `benchmark_workspace/runs/format_conformance_v1/` | 학습 형식 정합성: 평가 경로(vLLM·transformers·llama.cpp·Ollama)의 토큰이 학습 때와 같은지 검증 |
| `benchmark_workspace/runs/standard_v2/` | 표준 벤치 실행(사용자 모델 3종 + 공개 모델 19종): dev 설정 선택 → test A/B |
| `standard_tables/` | 결과 표(A 표, B 표; md·csv) — 수트 `reports/standard/`의 사본 |
| `SOURCES.json` | 복사한 파일의 원본 경로·SHA256, 정리(로그 진행 막대 제거)한 파일, 제외한 파일과 이유 |

다른 두 사용자 모델(frankenstallm, EVAFRILL-Mo-3B)과 공개 모델 기록도 같은 실행에서 나왔으므로 함께 둔다.

### 주요 결과 (JANSORI, base)

**1. 표준 벤치 A 표 — base 모델 비교** (lm-evaluation-harness 0.4.13 + vLLM 0.33.0 BF16, 같은 프롬프트·shot·로그우도 채점)

| 모델 | 활성/전체 B | 한국어 객관식 평균 | 확장 객관식 평균 | 영어 객관식 평균 | 한국어 bpb ↓ | 영어 bpb ↓ |
|---|---:|---:|---:|---:|---:|---:|
| kanana-2-30b-a3b-base | 3/30 | 74.7 | 45.8 | 74.2 | 0.662 | 0.597 |
| kanana-2-3b-base | 3.0/3.0 | 61.7 | 30.6 | 64.7 | 0.729 | 0.663 |
| **JANSORI** | 3.2/3.2 | 29.6 | 18.5 | 42.1 | **0.752** | 0.770 |
| kanana-1.5-2.1b-base | 2.1/2.1 | 57.0 | 23.4 | 61.7 | 0.761 | 0.679 |
| qwen3-30b-a3b-base | 3.3/30.5 | 72.1 | 44.6 | 77.5 | 0.789 | 0.557 |
| nemotron-3-nano-30b-a3b-base | 3/30 | 66.6 | 34.7 | 77.8 | 0.808 | 0.527 |
| EVAFRILL-Mo-3B pretrain | 2.94/2.94 | 28.4 | 17.9 | 31.6 | 0.818 | 1.167 |
| qwen3-4b-base | 4.0/4.0 | 64.1 | 34.5 | 71.3 | 0.914 | 0.642 |
| llama-3.2-3b | 3.2/3.2 | 43.9 | 21.6 | 64.1 | 0.949 | 0.615 |
| qwen2.5-3b | 3.1/3.1 | 56.8 | 28.2 | 67.0 | 0.998 | 0.676 |
| smollm3-3b-base | 3.1/3.1 | 50.4 | 21.8 | 68.0 | 1.042 | 0.624 |

- 한국어 객관식: KoBEST(5-shot)·KMMLU(5)·HAE-RAE·CLIcK·Belebele-ko·Global-MMLU-ko. 확장: KMMLU-Redux·KMMLU-Pro·KoBALT-700(0-shot). 영어: MMLU(5)·ARC-C(25)·HellaSwag(10)·Winogrande(5).
- bpb = bits-per-byte(토크나이저가 달라도 비교 가능). 한국어 wiki40b ko test 앞 500문서, 영어 wikitext-2 test. 표는 한국어 bpb 순.
- JANSORI 태스크별(%): KoBEST 58.7, KMMLU 20.4, HAE-RAE 20.4, CLIcK 31.0, Belebele 23.8, Global-MMLU-ko 23.2, KMMLU-Redux 24.6, KMMLU-Pro 21.6, KoBALT 9.1, MMLU 24.3, ARC-C 38.0, HellaSwag 52.6, Winogrande 53.6.
- 해석: 한국어 bpb는 공개 base 11종 중 3위(kanana-2 3B·30B-A3B 다음)이지만, 지식형 객관식(KMMLU·HAE-RAE·Belebele·Global-MMLU·KMMLU-Pro·KoBALT)은 무작위 수준이다. KMMLU 5-shot 20.4는 무작위(25)보다 낮고 원인은 조사하지 않았다. 신호가 있는 것은 KoBEST와 영어 HellaSwag·ARC다.
- 주의: wiki40b는 위키백과라 학습 데이터 오염 가능성이 있다. 공개 벤치는 공개 모델 학습에 섞였을 수 있다. 모든 행은 각 행의 문항별 bootstrap 95% CI·JANSORI 대비 paired 차이와 함께 `standard_tables/`에 있다.

**2. 설정 선택 효과** — 측정 전에 고정한 규칙(`dev_selection.yaml`)으로 dev 분할에서만 골랐다.

- 문서 시작 EOS(`docstart=eos`, 학습 때 문서 경계와 같은 조건) 선택: dev 평균 +0.69%p [−0.84, +2.24].
- test에서는 효과가 재현되지 않았다: 한국어 객관식 −0.46%p [−1.18, +0.26], 확장 −0.30 [−1.06, +0.46], 영어 +0.22 [−0.18, +0.63].
- 0-shot은 dev에서 −3.18%p [−4.41, −1.95]로 유의하게 나빴다.

**3. 형식 정합성** (`benchmark_workspace/runs/format_conformance_v1/REPORT.md`)

- JANSORI 배포 토크나이저·GGUF는 학습 토큰과 그대로 일치한다(텍스트 24/24, vLLM·transformers·llama.cpp·Ollama). BOS를 붙이지 않고 문서 끝에 `<|eos|>`를 붙이는 학습 형식을 따른다.

**4. 배포 검증과 상세 평가** (`jansori_release_v1/`, `jansori_detailed_eval_v1/`)

- 18개 파일 11,843,985,632바이트의 SHA-256을 비로그인 다운로드로 대조했다. vLLM BF16과 Ollama Q8_0·Q4_K_M의 생성·재로딩을 확인했다.
- KoBEST BoolQ test 1,404문항에서 참 예측 편향이 크다(정답 수 BF16 730, Q8_0 729, Q4_K_M 733).
- 코드 함수 12조건: 설명만 주면 0/12, 예시 두 개와 `return` 접두어를 주면 9/12.
- H200 단독 성능 27조건과 holdout PPL(한국어·영어 각 1,466문서) bootstrap 결과는 `jansori_detailed_eval_v1/DETAILED_RESULTS.md`, `PERFORMANCE.csv`에 있다.

### 재현

```bash
git clone https://github.com/pathcosmos/ko-llm-bench-suite && cd ko-llm-bench-suite && git checkout 4674cd4c593fbd4d970493eecda5e899b23dfe9b
bash <this-repo>/evaluation/benchmark_workspace/env/setup.sh        # vLLM·lm-eval·llama.cpp 등 (공개 2주 이상 버전 고정, env/lock.txt)
python -m kobench.standard.run --download --models jansori-bf16 qwen3-4b-base
python -m kobench.standard.run --models jansori-bf16 --suite ko_mc_v1      # ko_mc_ext_v1, ppl_v1, en_mc_v1
python -m kobench.standard.dev_search run --models jansori-bf16 && python -m kobench.standard.dev_search select
python -m kobench.standard.report --suite ko_mc_v1 --ref jansori-bf16 [--table B]
```

- `env/paths.sh`의 경로 기본값(`LT_PROJECT`, `LT_LOCAL`)은 원래 실행 환경 값이며 환경 변수로 바꿔 쓴다. JANSORI 경로는 `JANSORI_BF16`으로 지정한다.
- KMMLU-Redux/Pro는 HF gated 데이터셋(cc-by-nc-nd-4.0)이라 접근 승인이 필요하다.

### 포함하지 않은 것

- 문항별 결과(`*.samples.jsonl.gz`): 데이터셋 원문을 포함하고(KMMLU-Redux/Pro는 재배포 금지) 크기가 수백 MB다. 위 명령으로 다시 만들 수 있다.
- Ollama 서버 로그, pid/port 파일. 실행 로그의 진행 막대 중간 상태는 지웠다(100% 줄은 남김). 목록은 `SOURCES.json`.
- 기록 안의 상대 링크(`../runs/...`)는 원래 저장소 위치 기준이라 이 폴더에서는 일부가 맞지 않을 수 있다.

### 남은 과제

생성 벤치(HRM8K·IFEval-Ko 외), judge 기반 채점, 코드 격리 실행, 클라이언트 측 성능 비교, GGUF Q8/Q4의 llama.cpp 로그우도 비교, 2순위 공개 모델은 아직 실행하지 않았다.

## English

Post-release verification, detailed evaluation and standard-benchmark comparison records for JANSORI (the public 47,000-step checkpoint, [pathcosmos/JANSORI](https://huggingface.co/pathcosmos/JANSORI)).
The evaluation code lives in the general-purpose suite [pathcosmos/ko-llm-bench-suite](https://github.com/pathcosmos/ko-llm-bench-suite/tree/4674cd4c593fbd4d970493eecda5e899b23dfe9b) (branch `feat/standard-benchmarks`, commit `4674cd4c593fbd4d970493eecda5e899b23dfe9b`);
this folder keeps the configurations, scripts, logs and result tables produced with it, plus the release and evaluation records from the training repository.

### Layout

| Path | Contents |
|---|---|
| `jansori_release_v1/` | Hub release verification: SHA of 18 files, clean-cache download, generation and reload checks for BF16 (vLLM) and Q8_0/Q4_K_M (Ollama) |
| `jansori_detailed_eval_v1/` | Detailed per-format evaluation: 98 generation inputs, 12 code-function cases, KoBEST BoolQ test (1,404), holdout PPL bootstrap, 27 single-H200 performance conditions |
| `benchmark_workspace/` | Comparison workspace: environment rebuild (`env/setup.sh`, version lock), goal specs, run scripts and logs, work log (`CLAUDE.md`) |
| `benchmark_workspace/runs/format_conformance_v1/` | Training-format conformance: evaluation-path tokens match training tokens |
| `benchmark_workspace/runs/standard_v2/` | Standard benchmark runs (3 own models + 19 public models): dev setting selection, then test A/B |
| `standard_tables/` | Result tables (A and B; md and csv), copied from the suite's `reports/standard/` |
| `SOURCES.json` | Source path and SHA-256 of every copied file, cleaned files, excluded files and why |

### Key results (JANSORI, base)

- **Standard benchmarks, table A** (same prompts, shots and log-likelihood scoring for every model): Korean bits-per-byte 0.752 ranks 3rd of 11 public/own base models (after kanana-2 3B and 30B-A3B), but knowledge-heavy multiple choice (KMMLU, HAE-RAE, Belebele, Global-MMLU, KMMLU-Pro, KoBALT) is at chance level. Average Korean MC 29.6 vs 43.9–74.7 for public base models; English MC 42.1 vs 61.7–77.8. KMMLU 5-shot (20.4) is below chance (25); the cause has not been investigated. Signals: KoBEST 58.7, HellaSwag 52.6, ARC-C 38.0. See the Korean table above and `standard_tables/` (bootstrap 95% CIs and paired differences vs JANSORI).
- **Setting selection** (pre-registered rule, dev only): document-start EOS was selected on dev (+0.69 pp [−0.84, +2.24]) but showed no effect on test (Korean MC −0.46 [−1.18, +0.26], extended −0.30 [−1.06, +0.46], English +0.22 [−0.18, +0.63]). Zero-shot was significantly worse on dev (−3.18 pp).
- **Format conformance**: the released tokenizer and GGUF files reproduce the training tokens exactly (24/24 texts across vLLM, transformers, llama.cpp, Ollama).
- **Release and detailed evaluation**: all 18 files (11,843,985,632 bytes) verified by anonymous download; strong "true" bias on KoBEST BoolQ; code functions 0/12 from descriptions alone and 9/12 with two examples and a `return` prefix.
- Caveats: wiki40b is Wikipedia (possible training contamination); public benchmarks may be in public models' training data.

### Reproduction

See the commands in the Korean section. Path defaults in `env/paths.sh` reflect the original machine and can be overridden by environment variables. KMMLU-Redux/Pro are gated (cc-by-nc-nd-4.0).

### Not included

Per-item sample files (contain dataset text, some non-redistributable), Ollama server logs, pid/port files; intermediate progress-bar lines were removed from logs. Relative links inside the records (`../runs/...`) refer to their original repository locations.
