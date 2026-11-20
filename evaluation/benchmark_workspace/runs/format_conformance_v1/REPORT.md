# 학습 형식 정합성 검증 (Goal 1-A) — 2026-11-18

결론: **모든 평가 경로가 학습 코드와 토큰 ID 단위로 일치한다.**
- formats.py, vLLM(chat 템플릿), lm-eval 인코딩: 3종 모두 일치한다.
- llama.cpp와 Ollama (GGUF가 있는 JANSORI·frankenstallm):
  - JANSORI는 배포 GGUF 그대로 일치한다.
  - frankenstallm의 배포 GGUF v2는 학습 후 만든 토크나이저를 담고 있어 불일치한다. 그래서 다음 경로를 만들어 검증했다:
    - 토크나이저 메타데이터만 학습 토크나이저로 바꾼 GGUF + 클라이언트 공백 규칙 → 문자열 경로 일치
    - 학습 토큰 ID를 llama-server에 직접 넣는 경로 → 일치
    - 이 GGUF용 Ollama Modelfile → 일치
- EVAFRILL은 GGUF가 없다(llama.cpp 미지원 구조).
모델별 형식과 근거(파일:줄)는 `ko-llm-bench-suite/configs/standard/formats.yaml`에 있다.

## 1. 학습 형식 (근거는 formats.yaml)
| 모델 | 토크나이저 | 사전학습 문서 구분 | 대화 형식 | system |
|---|---|---|---|---|
| JANSORI | BBPE 65,536 (배포본 = 학습 샤드 생성 파일, SHA 050f9b43…) | `ids + [<\|eos\|>]`, BOS 없음 | 없음 (base) | – |
| frankenstallm | korean_sp Unigram 64,000 (SHA f413abc0…) — **배포 tokenizer.json 아님** | 혼합: 주력 소스는 구분 없음, 일부 EOS 가능 | SFT `<\|user\|>\n{u}\n<\|assistant\|>\n` + `{a}</s>`; 최종 ORPO는 BOS+원시 프롬프트(TRL) | 학습에서 무시됨 |
| EVAFRILL-Mo | korean_sp (같은 파일) | frankenstallm과 같은 파이프라인 | SFT·DPO·ORPO 모두 위 SFT 형식, 프롬프트와 응답을 따로 encode | 학습에서 무시됨 |

정정: 이전에 CLAUDE.md에 적은 "EVAFRILL 사전학습은 ids+[eos]"는 `data/tokenize_extra.py`만 보고 내린 판단이었다. 실제 주력 경로(`data/prepare.py`, `scripts/prepare_3b_data.sh`)는 구분 토큰 없이 이어 붙인다.

## 2. 새로 확인된 사실
1. **frankenstallm 배포 tokenizer.json은 학습 후(2026-03-26) byte-fallback 256개를 붙여 다시 만든 것**이다. 어휘와 분절은 학습 토크나이저와 같지만, 사전에 없는 문자를 `<unk>` 대신 byte 토큰으로 바꾼다. 이 byte 토큰 임베딩은 학습되지 않았다(노름 0.855±0.009, 일반 토큰은 2.33±0.20, tied) → `frankenstallm_byte_rows.log`.
2. **korean_sp는 개행·탭을 `<unk>`(3)로 인코딩한다.** `\n`, `\n\n`, `\t`가 모두 같은 `<unk>` 하나가 된다. 학습 템플릿의 개행도 `<unk>`로 학습됐다. 배포 토크나이저는 개행을 학습되지 않은 byte 토큰(64010)으로 바꾸므로, 개행이 많은 프롬프트(객관식 선택지 등)에서 결과가 크게 달라질 수 있다 → `newline_unk.log`.
   - 진행 중이던 frankenstallm의 ko_mc_v1(A 표)은 배포 토크나이저로 실행되었다. 이 결과는 "기존 방식" 기준선으로만 쓴다.
3. korean_sp(tokenizers)와 원본 SentencePiece(tokenizer.model)는 1,802문장 중 69개에서 다르다(NFKC 53, 공백 정리 16). 학습 주력 경로는 정규화 없는 tokenizers였다 → `tokenizer_diff_causes.log`.
4. lm-eval의 vLLM 백엔드는 빈 문맥(PPL 등)에 BOS를 붙인다. 3종 모두 BOS로 학습하지 않았으므로, 사용자 모델은 `prefix_token_id = EOS`로 실행하도록 `run.py`를 고쳤다.

## 3. 토큰 일치 검증 (`token_conformance.json`)
기준값: 원본 학습 코드 함수(frankenstallm·EVAFRILL `data/sft_dataset.py`의 `_build_conversation_turns` + `tokenizer.encode`, JANSORI `encode_batch(add_special_tokens=False)`)를 그대로 import해 만들었다. 입력: 텍스트 24개(한/영/공백/개행/탭/OOV/이모지/전각/코드/특수 토큰 문자열), 대화 29개(텍스트 24 + system·멀티턴·연속 user·선행 assistant).

| 모델 | formats.py | vLLM chat 템플릿 (문자열 / 토큰) | lm-eval tok_encode | llama.cpp 문자열 | llama.cpp 토큰 ID 입력 | Ollama (렌더링 → 내부 엔진 토큰 ID) |
|---|---|---|---|---|---|---|
| JANSORI (텍스트) | 24/24 | 24/24 (BOS 추가 없음) | 24/24 | **24/24** (배포 GGUF, add_special 유무 모두) | 24/24 | **24/24**, prompt_eval_count 24/24 |
| frankenstallm (대화) | 29/29 | 29/29 / 29/29 | 29/29 | 배포 GGUF 0/29 → **학습 토크나이저 GGUF 29/29** | 29/29 | 배포 GGUF 불일치 → **학습 토크나이저 GGUF 29/29**, prompt_eval_count 29/29 |
| frankenstallm (텍스트) | 24/24 | 24/24 | 24/24 | 배포 8/24 → **학습 토크나이저 GGUF 24/24** (+ 특수 토큰 경계 8/8) | 24/24 | – |
| EVAFRILL (대화/텍스트) | 29/29, 24/24 | 29/29 / 29/29, 24/24 | 29/29, 24/24 | GGUF 없음 | – | – |

- **학습 토크나이저 GGUF** (`kobench/standard/gguf_retokenize.py`, `$LT_LOCAL/gguf/frankenstallm-3b-v2-{Q8_0,Q4_K_M,f16}.train-tokenizer.gguf`):
  - 텐서 254개는 배포 GGUF와 바이트 단위로 같다. 토크나이저 메타데이터만 korean_sp Unigram(llama.cpp UGM)으로 바꿨다.
  - 어휘는 64,000 + 패딩 256(CONTROL)이다. BOS/EOS 자동 추가는 없고 `add_space_prefix=False`다.
  - llama.cpp UGM은 조각마다 ▁을 무조건 붙인다. 그래서 접두를 끄고, 클라이언트가 `formats.llamacpp_text()`로 HF 규칙을 재현한다: 특수 토큰으로 나눈 각 조각이 공백으로 시작하지 않으면 공백 하나를 붙인다.
  - 접두를 켠 첫 시도는 텍스트 21/24, 대화 27/29였다(`train_tokenizer_gguf_check.json`, 보존). 접두를 끄고 클라이언트 규칙을 적용한 결과는 24/24, 29/29, 8/8이다(`train_tokenizer_gguf_noprefix_check.json`).
- **토큰 ID 입력 경로**: llama-server /completion에 학습 토큰 ID 배열을 넣으면 `tokens_evaluated`가 입력 길이와 같다(53/53, 24/24). BOS를 명시적으로 붙인 입력과는 출력 분포가 모두 다르다(`llamacpp_token_id_path.json`).
- **Ollama**:
  - `_debug_render_only`로 Ollama가 렌더링한 문자열을 받아, Ollama가 띄운 내부 llama-server(번들 llama.cpp)의 /tokenize로 토큰화했다. 실제 실행의 prompt_eval_count와도 대조했다(`ollama_train_tok/ollama_token_ids.json`).
  - `tests/unit/test_formats.py::test_ollama_paths_match_training_ids`가 수트의 Modelfile 그대로 통과했다.
  - 참고: `/api/generate`의 `context`는 프롬프트와 응답을 합친 문자열을 다시 토큰화한 값이라, 프롬프트 토큰 기록으로 쓸 수 없다(JANSORI 코드 사례에서 끝 `\n`과 응답 `\n`이 `\n\n` 하나로 합쳐짐).
- 남은 한계: Ollama Go 템플릿은 조각별 규칙을 적용할 수 없다. 그래서 user 내용 안의 특수 토큰 문자열 바로 뒤가 공백이 아니면 ▁ 하나가 다르다(llama.cpp 직접 경로는 `llamacpp_text`로 일치).
- 대조군: frankenstallm 배포 tokenizer.json으로 같은 대화를 인코딩하면 29/29 모두 다르다(개행 → byte 토큰). 검증이 실제 차이를 잡아낸다는 뜻이다.
- 배포 GGUF v2(frankenstallm)의 불일치 원인은 세 가지다: BOS(1) 자동 추가, 개행을 byte 토큰으로 변환, 분절 알고리즘 차이(예: `어디인가요?` 학습 [43793,178] vs llama.cpp [38,13,335,178]). 증거 파일에서는 `RELEASE_GGUF_not_used__*` 키로 남겼다.
- (첫 시도, 배포 GGUF) Ollama의 학습 형식 Go 템플릿은 학습 문자열을 raw로 넣은 것과 prompt_eval_count가 28/29 같았다. 토큰 자체는 GGUF 토크나이저 때문에 달랐다 → `ollama/ollama_check.log`. 학습 토크나이저 GGUF로 해결했다(위).

## 4. 기존 수트와의 실제 차이 (예: "한국의 수도는 어디인가요?")
- 학습 형식(두 모델 공통): `▁< | us er | > <unk> 한국 의 ▁수도 는 ▁어디 인가요 ? <unk> < | assi s tant | > <unk>`
- 기존 EVAFRILL 경로(`kobench/evafrill_runner.py:217`, Track1 system 사용 시): `▁당신은 ▁한국어 ▁시험 … 하세요 . <unk> 한국 의 ▁수도 는 ▁어디 인가요 ?` → 역할 태그 없음, 학습에 없던 시스템 문장
- 기존 frankenstallm 경로(`modelfiles/Modelfile.v2-*` `TEMPLATE {{ .Prompt }}`, SYSTEM 무시): `<s> ▁한국의 ▁수도 는 ▁어디 인가요 ?` → 역할 태그 없음, 학습하지 않은 BOS. 위 토큰은 학습 토크나이저 기준이고, 실제 Ollama는 여기에 GGUF 토크나이저 차이(개행 byte, 분절)까지 더해진다.

## 5. 산출물
- `ko-llm-bench-suite/configs/standard/formats.yaml` — 모델별 형식, 근거, 평가 경로별 결과
- `ko-llm-bench-suite/kobench/standard/formats.py` — 프롬프트·토큰 생성, SHA 검증 토크나이저 디렉터리(`$LT_LOCAL/tokenizer_overrides/<model>`, HF 원본 수정 없음), 학습 형식 Jinja chat 템플릿
- `ko-llm-bench-suite/kobench/standard/conformance.py` — 이 검증의 재실행: `python -m kobench.standard.conformance --out <dir> --llamacpp`
- `ko-llm-bench-suite/modelfiles/Modelfile.frankenstallm-3b-v2-train-tokenizer` — 학습 토크나이저 GGUF용, 토큰 ID 일치 검증됨. `Modelfile.v2-{Q8_0,Q4_K_M,f16}`(배포 GGUF용)은 학습 형식 TEMPLATE와 SYSTEM 제거만 하고, 토큰 불일치 주의 문구를 넣었다. `Modelfile.jansori-3b` 신규
- `ko-llm-bench-suite/kobench/standard/gguf_retokenize.py` — 학습 토크나이저 GGUF 생성
- `ko-llm-bench-suite/tests/unit/test_formats.py` — 15개 (llama.cpp 2개와 Ollama 1개는 `KOBENCH_SLOW=1`; Ollama 테스트는 CPU Ollama를 직접 띄우고 수트 Modelfile로 모델을 만들어 검사한 뒤 정리한다)

## 6. Goal 2로 넘기는 결정 사항 (dev로 정한다)
- 로그우도 문서 시작 토큰: 없음 vs EOS (JANSORI는 학습상 EOS 쪽에 가깝고, 나머지 둘은 혼합)
- frankenstallm 대화 형식: SFT 템플릿(기본, 원저자 평가 형식) vs ORPO 원시+BOS
- system 메시지: 버림(학습 그대로, 기본) vs 첫 user에 앞붙임
- 생성 결과의 `<unk>`(3) 처리: 그대로 제거 vs `\n` 복원 (frankenstallm·EVAFRILL)
