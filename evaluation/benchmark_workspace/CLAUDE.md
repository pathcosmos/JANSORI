# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 목적

사용자가 직접 만든 LLM들을 **실행 가능한 최적화 상태로 세팅**하고 **심층 성능 평가**를 수행하는 작업 공간이다.
사용자는 한국어 화자다. **사용자에게는 한국어로 답한다.** 결과는 사실대로 보고하고, 실패·미검증 항목도 숨기지 않는다.

이 디렉터리는 2026-11-18 기준 비어 있는 상태에서 시작했다(git 저장소 아님). 평가 코드를 만들면 이 문서에 실행 명령을 추가한다.

진행 Goal(순서대로): `docs/goals/GOAL1.md`(Opus xhigh, 형식 정합성 + EVAFRILL 증분 디코딩 엔진 — **2026-11-18 종료**, 결과: `runs/format_conformance_v1/REPORT.md`, `runs/evafrill_engine_v1/REPORT.md`; 엔진 V3는 사용자 승인으로 기준 변경 후 통과, V4는 배치 불변 엔진으로 엄격 기준 통과. 사용자 결정으로 변경 기준에 따라 완료 처리됨) → `docs/goals/GOAL2.md`(Opus high, 최적 설정 탐색·비교 벤치·수트 업그레이드·보고). `/goal`에 넣는 4,000자 이내 요약본은 같은 폴더의 `*.goal.txt`이고, 상세 사양은 `.md`가 기준이다.

## 평가 대상 모델 (사용자가 만든 모델은 아래 3개가 전부다, HF 계정 `pathcosmos`)

| 모델 | 구조 | 단계 | 형식 / 실행 경로 |
|---|---|---|---|
| `pathcosmos/JANSORI` | Llama 호환 dense 3B (28층, hidden 3072, GQA 24/8, vocab 65,536, ctx 4096, untied) | **Base(사전학습만)**, chat 템플릿 없음 | BF16 safetensors → vLLM/transformers. Q8_0·Q4_K_M GGUF → Ollama/llama.cpp. Ollama 템플릿은 `{{ .Prompt }}`(원문 이어쓰기) |
| `pathcosmos/frankenstallm` | Llama 호환 dense 3B (vocab 64,256, tied embedding) | SFT → ORPO 지시 모델 | `model.safetensors` + `gguf/frankenstallm-3b-v2-{Q4_K_M,Q8_0,f16}.gguf`. **GGUF는 v2만 사용**(v1은 SPM byte_to_token 결함으로 llama.cpp에서 SIGABRT). 권장 샘플링: temp 0.7, repeat_penalty 1.2, top_p 0.9, top_k 50, stop `</s>`, 시스템 프롬프트는 `gguf/Modelfile.3b-v2-*` 참조 |
| `pathcosmos/EVAFRILL-Mo-3B` | Mamba-2 + Transformer 하이브리드 2.94B (26층 중 A 2개, 나머지 M; vocab 64,000) | 하위 폴더별 체크포인트: `pretrain/ sft-v2/ dpo-r1..r3/ orpo/ slerp/`, 루트는 최종 | **GGUF/Ollama 불가, vLLM 미지원.** GitHub `pathcosmos/EVAFRILL-Mo` 소스(`model/config.py`, `model/transformer.py`의 `LLM`)로 PyTorch 직접 추론. 채팅 형식 `<|user|>\n{msg}\n<|assistant|>\n` |

- 모델별 기본값이 다르다. JANSORI는 base라 chat 형식 프롬프트·지시 수행 평가를 그대로 적용하면 안 된다(이어쓰기/로그우도 기반 평가가 맞다). 지시 모델과 base를 같은 표에서 비교할 때는 이 차이를 명시한다.
- frankenstallm과 EVAFRILL-Mo는 같은 학습 토크나이저(korean_sp Unigram 64,000, SHA f413abc0…)로 학습했다. **frankenstallm의 HF 배포 tokenizer.json(byte-fallback 64,256)은 학습 후 바뀐 것이라 평가에 쓰지 않는다**: 개행·OOV 문자를 학습되지 않은 byte 토큰으로 바꾼다. 평가 경로는 `configs/standard/formats.yaml`과 `kobench/standard/formats.py`를 따른다(상세: `runs/format_conformance_v1/REPORT.md`).
- korean_sp는 개행·탭을 `<unk>`(3)로 인코딩한다(`\n`, `\n\n`, `\t` 모두 같은 `<unk>` 하나). 두 모델은 개행을 이렇게 학습했다.
- frankenstallm 배포 GGUF v2는 llama.cpp 토큰화가 학습과 다르다(BOS 추가, 개행 byte, 분절 알고리즘). 다음 두 경로는 학습 토큰과 정확히 일치함을 검증했다(`runs/format_conformance_v1/REPORT.md`):
  - (a) `python -m kobench.standard.gguf_retokenize --src <배포 gguf> --out <...>.train-tokenizer.gguf`로 만든 **학습 토크나이저 GGUF**(가중치 바이트 동일, korean_sp UGM, `add_space_prefix=False`)에 문자열을 `formats.llamacpp_text(text)`로 넣는다. 결과물은 `$LT_LOCAL/gguf/frankenstallm-3b-v2-{Q8_0,Q4_K_M,f16}.train-tokenizer.gguf`이고, Ollama는 `modelfiles/Modelfile.frankenstallm-3b-v2-train-tokenizer`를 쓴다.
  - (b) llama-server에 `formats.encode_prompt/encode_text` 토큰 ID 배열을 직접 넣는다.
  - JANSORI 배포 GGUF는 그대로 일치하고, Ollama는 `modelfiles/Modelfile.jansori-3b`를 쓴다.
- Ollama의 프롬프트 토큰을 확인하려면 `_debug_render_only`로 렌더링 문자열을 받아 Ollama 내부 llama-server(서버 로그의 `--port`) /tokenize로 토큰화한다(`conformance.check_ollama`). `/api/generate`의 `context`는 프롬프트와 응답을 합친 문자열을 다시 토큰화한 것이라 쓰면 안 된다.
- `runs/standard_v1`의 frankenstallm ko_mc_v1 결과는 배포 토크나이저로 실행한 것이라 "기존 방식" 기준선으로만 쓴다. 지금의 `run.py`는 사용자 모델에 formats.yaml 토크나이저와 `prefix_token_id=EOS`를 쓴다.
- JANSORI는 다른 토크나이저(65,536)를 쓰므로 PPL 비교는 토큰 기준이 아니라 문자/바이트 기준으로 정규화해야 한다.

## 비교 대상: 공개 다운로드 가능 모델

목표는 사용자 모델을 ① 비슷한 규모의 dense 3B급, ② 활성 파라미터 약 3B의 MoE(A3B급) 공개 모델과 같은 조건에서 비교하는 것이다. 아래 저장소는 2026-11-18에 HF API로 존재·라이선스·gated 여부를 확인했다. 새 모델이 계속 나오므로 실행 직전에 최신 버전을 다시 확인하고, 사용한 revision(commit SHA)을 결과에 고정한다.

**Dense 3B급** (base / instruct 쌍. base는 JANSORI, instruct는 frankenstallm·EVAFRILL과 비교)
| 계열 | base | instruct | 비고 |
|---|---|---|---|
| Qwen3.5 4B | `Qwen/Qwen3.5-4B-Base` | `Qwen/Qwen3.5-4B` | Apache-2.0, `Qwen3_5ForConditionalGeneration`(멀티모달 래퍼) |
| Qwen3 4B | `Qwen/Qwen3-4B-Base` | `Qwen/Qwen3-4B-Instruct-2507` | Apache-2.0 |
| Qwen2.5 3B | `Qwen/Qwen2.5-3B` | `Qwen/Qwen2.5-3B-Instruct` | Qwen 라이선스 |
| kanana-2 3B (한국어) | `kakaocorp/kanana-2-3b-base` | `kakaocorp/kanana-2-3b-instruct` | Qwen3 구조 |
| kanana-1.5 2.1B (한국어) | `kakaocorp/kanana-1.5-2.1b-base` | `kakaocorp/kanana-1.5-2.1b-instruct-2505` | Llama 구조, Apache-2.0 |
| SmolLM3 3B | `HuggingFaceTB/SmolLM3-3B-Base` | `HuggingFaceTB/SmolLM3-3B` | Apache-2.0 |
| Gemma 4 E4B | `google/gemma-4-E4B` | `google/gemma-4-E4B-it` | Apache-2.0, 실효 4B |
| Llama 3.2 3B | `meta-llama/Llama-3.2-3B` | `meta-llama/Llama-3.2-3B-Instruct` | **gated(수동 승인)** |
| Gemma 3 4B | `google/gemma-3-4b-pt` | `google/gemma-3-4b-it` | **gated(수동 승인)** |
| 기타 instruct만 | — | `microsoft/Phi-4-mini-instruct`, `LGAI-EXAONE/EXAONE-3.5-2.4B-Instruct`, `naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-1.5B`(gated auto) | EXAONE·HyperCLOVAX는 비상업 등 자체 라이선스 |

**MoE A3B급** (총 21–35B, 활성 약 3B)
| 계열 | base | instruct | 구조 |
|---|---|---|---|
| Qwen3.6 35B-A3B | (base 공개 확인 안 됨) | `Qwen/Qwen3.6-35B-A3B` | Qwen3.5-MoE |
| Qwen3.5 35B-A3B | `Qwen/Qwen3.5-35B-A3B-Base` | `Qwen/Qwen3.5-35B-A3B` | Qwen3.5-MoE |
| Qwen3 30B-A3B | `Qwen/Qwen3-30B-A3B-Base` | `Qwen/Qwen3-30B-A3B-Instruct-2507` | Qwen3-MoE |
| kanana-2 30B-A3B (한국어) | `kakaocorp/kanana-2-30b-a3b-base-2601` | `kakaocorp/kanana-2-30b-a3b-instruct-2601` | DeepseekV3 구조 |
| Nemotron 3.5 Lightning 30B-A3B | — | `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16` | NemotronH(Mamba 하이브리드 MoE, EVAFRILL과 같은 계열 비교군) |
| Nemotron 3 Nano 30B-A3B | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-Base-BF16` | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16` | NemotronH |
| ERNIE 4.5 21B-A3B | `baidu/ERNIE-4.5-21B-A3B-Base-PT` | `baidu/ERNIE-4.5-21B-A3B-PT` | Apache-2.0 |

### 비교 원칙
- **base끼리, instruct끼리** 비교한다. base는 로그우도·PPL·few-shot 이어쓰기로, instruct는 각 모델 공식 chat 템플릿을 적용한 생성으로 채점한다. thinking 모드가 있는 모델은 끈 상태와 켠 상태를 구분해 기록한다.
- 같은 엔진, 같은 정밀도(BF16 기준. 양자화 비교는 별도 축), 같은 프롬프트·shot 수·디코딩 설정·최대 길이를 쓴다. 사용자 모델의 최대 문맥이 4096이므로 공통 평가 길이도 4096 이내로 맞춘다.
- 토크나이저가 모두 다르므로 PPL은 문자/바이트당 NLL(bits-per-byte)로 정규화한다.
- A3B는 품질뿐 아니라 **총 파라미터(메모리)와 활성 파라미터(연산)**를 함께 보고한다: VRAM, TTFT, tok/s, 동시 요청 처리량. BF16 30–35B는 약 60–70 GB라 H200 1장에 들어가지만 KV 캐시 여유를 기록한다.
- 학습 데이터 오염 가능성(공개 벤치마크)을 결과 해석에 명시한다.

### 실행 전 확인할 것
- Qwen3.5/3.6, Gemma 4, kanana-2(DeepseekV3), NemotronH, SmolLM3, EXAONE 4 등은 시스템의 transformers 4.55.2로 로드되지 않을 수 있다. 최신 transformers·vLLM을 별도 venv에 설치하고 모델별 로드 결과를 기록한다. NemotronH는 mamba_ssm/causal_conv1d 커널이 필요하다.
- gated 모델은 사용자의 HF 계정 승인이 필요하다. 승인되지 않은 모델은 건너뛰고 그 사실을 보고한다.
- 30B급 다운로드는 모델당 약 60–70 GB다. 로컬 디스크에 캐시를 두고 `HF_HOME`을 명시한다.

## 기존 자원 (재사용, 다시 만들지 말 것)

- 학습 저장소: `../llm-traditional` (JANSORI를 만든 곳). 그 CLAUDE.md·`docs/STATUS.md`에 전체 이력이 있다.
- JANSORI 로컬 배포본: `../llm-traditional/runs/3b_47k_hub_release_v1/nfs-release-v1/{bf16,gguf}` (HF 배포본과 SHA 동일). 실행법은 같은 폴더 `USAGE.md`.
- JANSORI 기존 상세 평가(기준선): `../llm-traditional/runs/jansori_detailed_eval_v1/` — `DETAILED_RESULTS.md`, `PERFORMANCE.csv`, 채점 코드 `grade.py`·`boolq.py`·`run.py`·`dual.py`. 요약: holdout PPL ko/en BF16 9.09/10.22, KoBEST BoolQ 약 52%(참 편향), H200 단독 BF16 vLLM 약 100 tok/s(동시 1).
- 기존 벤치 수트: GitHub `pathcosmos/ko-llm-bench-suite` (`frankenstallm_test`의 후속, v0.2.0, `kobench.py` + `kobench/`). 아래 "벤치 수트 점검 결과"를 반영해 발전시키는 것이 기본 방향이다.

## 벤치 수트 점검 결과 (ko-llm-bench-suite @ e767f9a, 2026-11-18)

구성: Ollama 중심 7개 트랙(T1 KoBEST/KMMLU, T2 Ko-Bench 멀티턴 + LLM judge, T3 한국어 심층 100문항, T4 코드·SQL·디버깅·수학, T5 일관성, T6 성능, T7 pairwise Bradley-Terry Elo), 이중 judge(qwen2.5:7b + exaone3.5:7.8b), YAML 설정, 체크포인트, FastAPI 대시보드, mock 기반 pytest.

**결과를 왜곡하는 문제 (먼저 고친다)**
1. **T1 자체 문항의 정답 위치 편향**: lm_eval이 없으면 직접 만든 문항으로 대체하는데, 정답 인덱스가 KMMLU 49/50, HellaSwag 20/20, COPA 19/20이 0번(A)이다. 항상 "A"라고 답하는 모델이 거의 만점을 받는다. 또 이것은 실제 KoBEST/KMMLU가 아닌데 같은 이름으로 보고된다. → 실제 데이터셋(`skt/kobest_v1`, `HAERAE-HUB/KMMLU` 등)을 고정 revision으로 쓰고, 선택지 순서를 섞고, 이름을 분리한다.
2. **T1이 생성 후 문자열을 파싱하는 방식**: 선택지 정규식 `[A-Da-d]`가 영어 단어 안의 소문자 a·d에도 걸린다(예: "answer" → A). base 모델(JANSORI)은 이 형식을 따르지 못해 실력과 무관하게 0점 근처가 나온다. → 선택지 로그우도 채점을 기본으로 하고, 생성 채점은 instruct 전용 보조 지표로 둔다. lm_eval 경로도 `--num_fewshot 0` 고정에, Ollama `/v1/completions`는 logprob을 주지 않아 loglikelihood 태스크가 사실상 동작하지 않는다.
3. **스코어카드 누락**: `scoring.build_scorecard`가 하드코딩된 `config.ALL_MODELS`만 순회하므로 YAML/`--models`로 추가한 모델(JANSORI, 비교 모델)은 종합표에서 빠진다.
4. **비교 조건 불일치**: 비교 모델은 Ollama 기본 태그(대부분 Q4_K_M), EVAFRILL은 BF16 PyTorch라 정밀도와 엔진이 섞여 있다. 기본 목록에는 크래시가 알려진 frankenstallm v1 GGUF도 들어 있다.
5. **수학 채점 대체 규칙**: 답 패턴을 못 찾으면 응답의 마지막 숫자를 답으로 본다. 그래서 오답이 우연히 정답으로 처리될 수 있다. → 최종 답 형식을 강제하고, 형식을 지키지 않은 응답은 별도로 집계한다.

**확장이 필요한 부분**
- 백엔드: `kobench/backends/`의 추상화는 있지만 트랙들은 `runner.generate`가 Ollama REST를 직접 호출하고, `validate_config`도 `ollama`만 허용한다. HF transformers(로그우도·PPL), vLLM(OpenAI 호환, logprobs), EVAFRILL 백엔드를 같은 인터페이스로 넣어야 A3B·최신 아키텍처를 BF16으로 비교할 수 있다.
- base 모델 트랙: bits-per-byte PPL(한·영 holdout, JANSORI 기존 1,466문서 재사용), few-shot 로그우도 MC(KoBEST 5종, KMMLU, HAE-RAE, CLIcK, Belebele-ko 등), 이어쓰기 품질.
- 통계: 문항별 결과 저장 + paired bootstrap 신뢰구간(JANSORI 평가의 `boolq.py`·`ppl-bootstrap` 방식), 데이터 revision과 실행 설정의 SHA 기록.
- Judge: 7–8B judge 두 개는 약하다. 더 강한 로컬 judge(예: A3B instruct)를 쓰되 평가 대상과 같은 계열이면 자기 선호 편향을 표시한다. 수학·사실 문항은 judge 대신 정답 기반으로 채점한다. T7의 정·역방향 위치 교차는 이미 구현되어 있다.
- T4 코드 실행은 임시 파일을 `python3`로 돌리는 수준(시간 제한만 있음)이다. 자원·네트워크·파일 제한을 둔 격리 실행이 필요하다(JANSORI `code_case.py` 방식 참고).
- T6 성능: TPS는 Ollama가 보고한 값의 평균이고 동시성은 최대 4까지다. 엔진에 의존하지 않는 클라이언트 측 TTFT·TPOT·처리량, 예열 분리, VRAM 최고치, 동시 요청 8–32를 측정하고 A3B와 dense를 같은 표에 둔다.

## 현재 실행 환경 (2026-11-18 확인)

- GPU **H200 1장**(143 GB), CPU 12, cgroup 메모리 약 240 GiB(`/sys/fs/cgroup/memory.max`). OOM 확인은 `/sys/fs/cgroup/memory.events`의 `oom_kill`.
- 시스템 Python 3.12, NGC torch 2.7.0a0(CUDA 사용 가능), transformers 4.55.2, flash_attn 2.7.3, `hf` CLI.
- `/home/work/llm-dense`는 NFS(1.9T)다. 스크립트·결과는 NFS의 이 디렉터리에, venv·모델 캐시는 로컬 overlay `/workspace/llm-test`(여유 2.4T)에 둔다. overlay는 **컨테이너가 다시 만들어지면 사라진다**(이전 `/tmp` 환경이 그렇게 없어졌다). 그래서 재구성은 `env/setup.sh`만으로 가능해야 한다.
- HF 인증은 기존 `~/.cache/huggingface/token`을 `env/paths.sh`가 `HF_TOKEN_PATH`로 넘긴다. 토큰 값을 파일·로그·커밋에 쓰지 않는다.

### 환경 명령
```bash
source env/paths.sh                 # 모든 셸에서 먼저. LT_LOCAL, HF_HOME, OLLAMA_MODELS, PATH(uv·ollama) 설정
bash env/setup.sh                   # 전체 구축 (단계별 .done 마커 → 재실행 시 건너뜀)
bash env/setup.sh vllm              # 단계만: uv | vllm | mamba | ollama | evafrill | cudaext | llamacpp | freeze
source $LT_VENV/bin/activate        # vLLM·lm-eval·최신 transformers venv (시스템 NGC torch와 분리)
ollama serve                        # paths.sh를 읽은 셸에서 (모델 저장소는 $OLLAMA_MODELS)
llama-server / llama-perplexity / llama-bench   # $LT_BIN (llama.cpp CUDA sm_90)
```
- 버전 정책: 공개 2주가 지난 릴리스만 쓴다. `setup.sh`의 `EXCLUDE_NEWER`(uv `--exclude-newer`)로 전이 의존성까지 고정하고, 결과는 `env/lock.txt`에 기록한다. 고정 버전: uv 0.13.7, vLLM 0.33.0(torch 2.14 cu130, transformers 5.20.2), lm-eval 0.4.13, Ollama v0.40.15, llama.cpp b12084(commit f9448cd), causal-conv1d 1.7.0, mamba-ssm 2.3.2.post1.
- 시스템에는 `zstd`·sudo·venv 내 pip가 없다. Ollama tar.zst는 `uvx --from zstandard`로 풀고, sdist는 PyPI JSON의 URL·sha256으로 내려받아 검증한다.
- 시스템 Python(NGC torch 2.7)은 JANSORI 학습 코드용이다. 평가에는 venv를 쓴다.
- **CUDA 확장 빌드(`cudaext`)에서 겪은 문제와 해결** (재구성 시 그대로 적용됨):
  - venv torch 2.14+cu130용 prebuilt wheel이 없고, 시스템 nvcc는 12.8이다 → 빌드 전용 툴킷 `cuda-toolkit[all]==13.0.3.0`을 `$LT_LOCAL/cuda13-tk`에 따로 설치한다. venv에 딸려 온 nvcc 13.4는 13.0 헤더와 섞여 "headers are incompatible" 오류가 났다.
  - 두 패키지의 setup.py가 sm_75~sm_110 gencode와 `-std=c++17`을 하드코딩한다 → sm_90 하나와 C++20으로 패치한다(torch 2.14 헤더가 C++20을 요구).
  - `MAMBA_SKIP_CUDA_BUILD` 설치는 import할 때 `selective_scan_cuda`가 필요해 실패하고, vLLM 의존성을 하위 버전으로 바꿔 버린다. 쓰지 않는다.
- llama.cpp는 `-DLLAMA_USE_PREBUILT_UI=OFF`로 웹 UI 없이 빌드한다. 얕은 클론이라 버전이 b1로 해석되어 'latest' UI 에셋을 받았고, 그 파일 구성이 맞지 않아 실패했다.
- Ollama는 프롬프트 logprob을 주지 않는다 → GGUF 형식의 로그우도·PPL 채점은 llama.cpp(`llama-server`, `llama-perplexity`)로 한다.
- 환경 확인 기록: `runs/env_smoke_v1/` (vLLM 생성: JANSORI 로딩 216초(NFS 첫 읽기+컴파일), frankenstallm 60초. llama-bench frankenstallm-v2 Q4_K_M: pp512 18.2k tok/s, tg128 451 tok/s).

## 표준 벤치 (ko-llm-bench-suite `feat/standard-benchmarks` 브랜치, `kobench/standard/`)
```bash
cd ko-llm-bench-suite && source ../env/paths.sh && export LT_SRC
$LT_VENV/bin/python -m kobench.standard.run --list                                   # 모델·스위트 목록, 고정 여부
$LT_VENV/bin/python -m kobench.standard.run --pin --models qwen3-4b-base ...          # 공개 모델 revision을 HF 현재 commit으로 고정
$LT_VENV/bin/python -m kobench.standard.run --models jansori-bf16 --suite ko_mc_v1     # 실행 (완료 태스크는 건너뜀, --force)
$LT_VENV/bin/python -m kobench.standard.run --models evafrill-mo-3b --suite smoke --limit 50
$LT_VENV/bin/python -m pytest -q -p no:cacheprovider tests/unit/test_standard.py      # 단일 테스트 파일
KOBENCH_GPU=1 KOBENCH_SLOW=1 $LT_VENV/bin/python -m pytest -q -p no:cacheprovider tests/   # 전체 554개 (GPU 엔진 + llama.cpp + 자체 기동 Ollama 토큰 검사, 약 2분)
$LT_VENV/bin/python -m kobench.standard.conformance --out ../runs/format_conformance_v1 --llamacpp   # 학습 형식 토큰 일치 재검증
```
- 설정: `configs/standard/models.yaml`(모델, revision, base/instruct, 전체·활성 파라미터, 백엔드), `configs/standard/suites.yaml`(태스크별 few-shot과 그 근거). 스위트는 `ko_mc_v1`, `en_mc_v1`, `smoke`.
- 모델은 한 번만 로드하고 태스크마다 `lm_eval.simple_evaluate(model=<LM 인스턴스>)`를 호출한다. 결과는 `results/standard/<model>/<task>__<n>shot[__limitN].json`(+ `.samples.jsonl.gz` 문항별)에 원자적으로 기록되고, 파일이 있으면 완료로 본다.
- MC는 모두 로그우도로 채점하고 chat 템플릿을 쓰지 않는다(base·instruct 공통). 공개 모델은 revision이 고정되지 않으면 실행을 거부한다.
- EVAFRILL 백엔드(`evafrill_lm.py`, lm-eval 이름 `evafrill`): 원본 `LLM`을 strict 로드한다. pretrain 체크포인트는 TransformerEngine 키 형식이라 원본 `_convert_te_state_dict`로 변환한다(README의 strict=False 로드는 이때 FFN 가중치를 조용히 빠뜨린다). 토크나이저 SHA를 검사한다. 문서 시작 EOS는 기본으로 붙이지 않고(`doc_prefix_eos=False`) Goal 2에서 dev로 정한다. 생성(`generate_until`)은 증분 디코딩 엔진(`evafrill_engine.py`)으로 한다.
- 종료 시 vLLM 소멸자가 SIGABRT(134)를 내므로 `run.py`는 결과를 기록한 뒤 `os._exit`로 끝낸다.
- 기존 `tests/conftest.py`는 `yaml`·`tokenizers`·`safetensors`를 무조건 MagicMock으로 바꿨다. 지금은 실제 모듈이 없을 때만 mock한다.
- 집계: `python -m kobench.standard.report --suite ko_mc_v1 --ref jansori-bf16` → `reports/standard/<suite>.{md,csv}`. base/instruct 표를 분리하고, lm-eval 점수와 문항 micro 정확도 + bootstrap 95% CI, 기준 모델 대비 paired 차이를 낸다.
- 공개 모델 30개의 revision은 2026-11-18에 고정했다(`--pin`). `--download`는 가중치·설정·토크나이저만 받는다. 공개 모델 30개 전부 다운로드 완료(gated였던 Llama-3.2 2개도 승인 후 받음).
- gated 데이터셋 KMMLU-Redux(2,587)와 KMMLU-Pro(2,822)는 접근 승인 완료(비상업 라이선스). lm-eval 0.4.13에는 태스크가 없고 test 분할만 있다 → 0-shot 커스텀 태스크로 추가해야 한다.
- 이미 고친 기존 수트 버그 (같은 브랜치):
  - T1 자체 문항의 정답 위치 편향: 선택지를 결정적으로 회전해 정답 위치를 균등하게 했다(`_balance_answer`).
  - 점수 키에 `custom_` 접두어를 붙여 실제 KoBEST/KMMLU와 구분했다.
  - 선택지 정규식: 단어 안의 소문자 a/d를 잡던 문제를 고쳐 독립된 대문자만 인정한다(응답 전체가 한 글자일 때만 소문자 허용).
  - `build_scorecard`가 `ALL_MODELS` 밖의 모델을 버리던 문제를 고쳤다. 테스트는 535개 통과.
- 모델 리비전 고정: frankenstallm `b9f7777f01479b18e5a3a55f9cad4582df122f7d`, EVAFRILL-Mo-3B `e95edece955af9df9a6215210db594a9984fddf5`. JANSORI는 NFS 배포본(HF `e68bfa3c…`와 SHA 동일)을 쓴다. EVAFRILL 소스는 `$LT_SRC/EVAFRILL-Mo`에 있고, 커밋은 `env/evafrill-commit.txt`에 기록된다.

### Goal 2 진행 기록
- frankenstallm ko_mc_v1을 학습 토크나이저로 재실행 완료(`results/standard/frankenstallm-bf16`). 이전(배포 토크나이저) 결과는 `results/standard_baseline_release_tokenizer/`에 보존.
- 1단계 dev 탐색: 규칙 `configs/standard/dev_selection.yaml`(측정 전 고정), 실행 `runs/standard_v2/dev_search_*.sh`, 선택 `python -m kobench.standard.dev_search select` → `configs/standard/best_settings.yaml`. 변형 결과는 `results/standard/<model>/v_<variant>/`.
  - 2026-11-18 결과(dev 평균, A 대비 paired bootstrap 95% CI): JANSORI는 docstart=eos 선택(+0.69%p [−0.84, +2.24], 0-shot은 −3.18%p로 유의하게 나쁨). frankenstallm은 A 유지(EOS −0.81, 0-shot −0.95, chat −2.01%p).
  - EVAFRILL 체크포인트 7개는 33.49~33.92로 차이가 작다. 규칙상 최고는 pretrain(slerp 대비 +0.23%p [−0.66, +1.20]). instruct 단계 중 최고는 dpo-r2(+0.19%p [−0.00, +0.38]). pretrain의 2차 변형은 A 유지(EOS −0.03, 0-shot −1.67%p). 객관식 1단계 선택 완료.
- 사용자 모델 test 실행: `runs/standard_v2/test_user_models.sh`(A: ko_mc_v1·ko_mc_ext_v1·ppl_v1·en_mc_v1, B: JANSORI docstart=eos). PPL은 A에서 이미 문서 앞 EOS로 계산하므로 B를 따로 돌리지 않는다.
  - 2026-11-19 완료. 주요 관찰: 지식형 객관식(KMMLU, HAE-RAE, Belebele, Global-MMLU, KMMLU-Pro, KoBALT)은 세 모델 모두 무작위 수준 근처다. 신호가 있는 것은 KoBEST(JANSORI 58.7)와 영어 HellaSwag·ARC(JANSORI 52.6/38.0, 나머지 약 30/23), PPL(bpb wiki40b_ko JANSORI 0.752 < frankenstallm 0.795 < EVAFRILL 0.82)이다. JANSORI KMMLU 5-shot 20.4는 무작위(25)보다 낮다(원인 미조사).
  - JANSORI B(docstart=eos)는 test에서 효과 없음: ko_mc_v1 −0.46%p [−1.18, +0.26], ko_mc_ext −0.30 [−1.06, +0.46], en_mc +0.22 [−0.18, +0.63]. dev 선택 효과가 재현되지 않았다.
- 생성 태스크(`kobench/standard/tasks/gen`, 스위트 `gen_v1`): `hrm8k_strict`(lm-eval hrm8k 프롬프트, 채점은 `exact_match_strict`=\\boxed 필수 / `exact_match`=원본(마지막 숫자 대체) / `format_fail` 분리), `ifeval_ko`(allganize/IFEval-Ko, lm-eval IFEval 채점기). 채점 의존성은 `env/setup.sh evaldeps`(langdetect, immutabledict, nltk punkt_tab). GPU 실행 검증은 아직 안 함.
- 커밋: `feat/standard-benchmarks`를 원격에 push함(사용자 승인, 2026-11-19 `307de56`). 결과는 요약 JSON만 커밋하고 문항별 `*.samples.jsonl.gz`는 커밋하지 않는다(데이터셋 원문 포함: KMMLU-Redux/Pro는 gated·cc-by-nc-nd, 수백 MB, 저장소가 PUBLIC). `--limit` 스모크 결과도 제외(.gitignore).
- 공개 모델 로드 문제: kanana-1.5는 transformers 5.20 LlamaConfig 검증(hidden % heads)이 명시된 head_dim을 무시해 거부 → models.yaml `relax_head_dim_check: true` + `run.relax_head_dim_check()`. EXAONE-3.5는 config가 저장소 코드라 `trust_remote_code: true`(configuration_exaone.py는 config 클래스만, 확인함). 1차 실패 로그는 `runs/standard_v2/public_p1/kanana-1.5-*`에 보존, 재시도는 `public_p1_retry1`(본 대기열 종료 후 PID 대기로 자동 실행).
- 1순위 공개 모델 19개 객관식·PPL 완료(2026-11-20 08:01): `runs/standard_v2/public_p1.sh`(dev 변형 → test A, 로그 `public_p1/`), kanana-1.5 재시도 `public_p1_retry1/`(13건 모두 성공). OOM 0.
  - 공개 모델 dev 선택(`dev_search select`): 17개 A, smollm3-3b chat(+0.92%p), llama-3.2-3b-instruct chat(+0.39%p). 모든 모델에서 shots=0이 A보다 나쁨. B test 실행: `runs/standard_v2/public_p1_b.sh`(chat=1, ko_mc·ko_mc_ext·en_mc).
  - A 표: `python -m kobench.standard.report --suite <s> --ref jansori-bf16 [--table B]` → `reports/standard/<s>[__B].{md,csv}`. report는 B 표(best_settings 변형, 같은 모델 A 대비 ΔA)와 bits_per_byte 코퍼스 비율 micro·CI(문서 재표집)를 지원(2026-11-20 수정; 이전엔 PPL 문항값이 쌍이라 micro가 틀렸음).
  - ko_mc_v1 평균(lm-eval): base 1위 kanana-2-30b-a3b-base 74.7, dense 1위 qwen3-4b-base 64.1, JANSORI 29.6, EVAFRILL pretrain 28.4. instruct: kanana-2-30b-a3b 74.9, frankenstallm 29.2. 한국어 bpb: kanana-2-30b-a3b-base 0.662 < kanana-2-30b-a3b 0.718 < kanana-2-3b-base 0.729 < JANSORI 0.752.
- 실행기 `run.py --variant 'docstart=eos,chat=1,shots=0'`. 커스텀 태스크: `kobench/standard/tasks/{dev,custom}`(KMMLU-Redux/Pro, KoBALT-700, wiki40b_ko bpb). 스위트 `dev_mc_v1`, `ko_mc_ext_v1`, `ppl_v1` 추가.
- 주의: 백그라운드 대기에 `pgrep -f "<명령 문자열>"`을 쓰면 대기 셸 자신의 명령줄에도 걸려 영원히 기다린다(2026-11-18 1시간 정지). PID로 기다린다.

### 모델별 최적화 과제
- **EVAFRILL-Mo**: 원본 forward에는 캐시가 없어 생성이 O(n²)다. → **증분 디코딩 엔진 `kobench/standard/evafrill_engine.py` 완성 (Goal 1, 2026-11-18)**. 상세: `runs/evafrill_engine_v1/REPORT.md`.
  - 사용법: `EvafrillEngine(model, max_batch=32, max_len=4608, batch_invariant=True).generate(prompts_ids, max_new_tokens, eos_ids=(2,), stop_strings, decode_fn, use_graph=True)`. lm-eval에서는 `EvafrillLM.generate_until`이 이 엔진을 쓴다(`gen_batch_size` 기본 32). 프롬프트 토큰은 `formats.encode_prompt('evafrill-mo-3b', messages)`로 만든다.
  - **배치 불변 모드(기본)**: decode를 (max_batch 행, max_len 어텐션)으로 고정 실행한다. 같은 프롬프트의 결과는 배치에 함께 든 다른 프롬프트 수와 무관하게 비트 단위로 같다. 평가 재현성용이다. 속도만 필요하면 `batch_invariant=False`(단일 스트림 약 275 vs 165 tok/s).
  - 최종 검증(`runs/evafrill_engine_v1/` 최상위 `REPORT.md`, `verification.json`, `verification_final.json`, `perf.csv` = 최종 엔진. 실행 로그 `v2_batch_invariant/`, 이전 엔진 `v1_batch_variant/`): 3개 체크포인트 모두 V1(prefill 원본과 비트 동일)·V2(decode 256단계 오차 원본 BF16 수준)·V3(사용자 승인 변경 기준: 교사 강제 결정 일치 + 분기는 margin <0.5)·V4(배치 8/32가 배치 1과 토큰·로짓 비트 동일, Goal 문구 그대로) 통과. CUDA graph = eager(비트 동일).
  - V3 사전 등록 기준(자유 greedy 6/8 동일)은 pretrain 1/8, sft-v2 5/8, slerp 6/8이었다. 원본 자신도 같은 위치 로짓이 입력 길이에 따라 최대 0.28 달라져, 캐시 엔진으로는 원리적으로 보장할 수 없다. 원래 결과·기준 파일은 보존했다.
  - 성능(slerp, H200): 원본 대비 배치 불변 4.0~80배, 크기 맞춤 5~105배. 입력 3968·배치 32에서 원본은 메모리 부족, 엔진은 13.7 GiB. 한계: 시퀀스별 prefill이라 큰 배치의 TTFT가 길다(128토큰·배치 32: 0.97초).
  - 엔진은 원본 RoPE 규칙을 따른다(길이 ≤4096은 bf16으로 변환된 캐시 표, 초과는 fp32 표). 원본 모듈은 클래스 정의 모듈의 전역(`type(layer).forward.__globals__`)으로 참조한다. 테스트의 conftest가 `model.*`를 MagicMock으로 바꾸기 때문이다.
- **frankenstallm / JANSORI**: Llama 호환이라 vLLM BF16 + Ollama GGUF로 바로 실행할 수 있다. frankenstallm은 tied embedding이다(`tie_word_embeddings=true`).

## 작업 규칙 (llm-traditional에서 이어받음)

- 측정은 같은 GPU를 한 형식/한 서버씩 단독으로 사용하고, 예열 후 요청만 집계한다. 서버 기동·로딩 시간은 별도로 기록한다. 실행 설정(엔진 버전, dtype, KV 캐시, 배치, 샘플링, 시드)을 결과와 함께 JSON으로 남긴다.
- 실패 기록은 덮어쓰지 않고 보존하며, 재시도는 새 run 디렉터리에 남긴다. 장시간 작업은 재시작 가능(`.done` 마커 등)하게 만든다.
- 장시간 백그라운드 작업은 짧은 간격으로 확인하고, 로그가 멈추면 OOM부터 확인한다.
- **사용자 확인 후에만**: HF 업로드나 모델 카드 수정, 다른 프로젝트(`../llm-traditional` 등)의 산출물 삭제·덮어쓰기.
