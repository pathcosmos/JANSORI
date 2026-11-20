# JANSORI 세 형식 시험과 다음 모델 카드 업데이트

대상은 `pathcosmos/JANSORI`의 47,000스텝 Base 모델이다. 이번 비교의 가중치와 토크나이저는 [배포 커밋 e68bfa3](https://huggingface.co/pathcosmos/JANSORI/tree/e68bfa3cb31e9589f4481a2e31d6d75c731fec9d)에 고정한다. 아래 후속 시험의 후보값을 현재 배포본의 검증된 기본값으로 해석하지 않는다.

## 이미 측정한 기준선

holdout 1,466문서에서 한국어 663,905개, 영어 705,634개의 다음 토큰을 같은 ID와 마스크로 채점했다. NLL은 유효 표적당 평균 음의 로그우도이고 PPL은 그 지수다. 속도는 H200 첫 순차 회차의 고정 요청 6개, 요청당 최대 64토큰에서 측정했다. 학습용 전체 체크포인트는 Hub 전송 대상에서 제외했다.

| 형식·런타임 | 파일 용량(GB) | 한국어 NLL / PPL | 영어 NLL / PPL | 최초 로딩(초) | 평균 요청(초) | 생성 토큰/초 | 생성 중 해당 GPU 최고(MiB) |
|---|---:|---:|---:|---:|---:|---:|---:|
| BF16 · vLLM | 6.448 | 2.20715 / 9.08981 | 2.32393 / 10.21574 | 87.10 | 0.638 | 100.27 | 8,805.9 |
| Q8_0 · Ollama | 3.426 | 2.20727 / 9.09086 | 2.32408 / 10.21729 | 23.65 | 4.418 | 14.49 | 5,055.9 |
| Q4_K_M · Ollama | 1.970 | 2.21880 / 9.19630 | 2.33387 / 10.31774 | 23.53 | 6.432 | 9.95 | 3,761.9 |

BF16 용량은 설정·토크나이저를 포함한다. vLLM 로딩은 서버 시작부터 준비까지, Ollama 로딩은 실행 중인 서버에 모델 적재를 요청한 시점부터 응답까지다. 형식과 런타임이 함께 달라지므로 세 행의 속도로 양자화만의 효과를 추정할 수 없다. 동일 GGUF 실행기에서 고정밀 원본·Q8·Q4를 추가 비교하면 양자화에 따른 변화에 더 가깝게 접근할 수 있다. [세부 측정](../nfs-release-v1/REPORT.md)

## 배포가 제대로 되었는지 시험하는 순서

1. 공개 저장소를 비로그인으로 조회하고 위 커밋과 18개 파일·크기·SHA-256을 대조한다. vLLM과 Ollama 모두 새 캐시에서 시작한다. 가중치가 화면에 표시되는 것과 생성 요청 성공을 각각 기록한다.
2. vLLM은 `--revision`과 `--tokenizer-revision`을 같은 값으로 지정하고 `--load-format safetensors --dtype bfloat16 --tensor-parallel-size 1 --max-model-len 4096`으로 GPU 0 한 장에서 실행한다. OpenAI 호환 `/v1/completions`에서 일반 문자열을 전송한다.
3. Ollama는 GPU 1에만 서버를 띄우고 `hf.co/pathcosmos/JANSORI:Q8_0`과 `:Q4_K_M`을 차례로 실행한다. 각 태그가 가리키는 실제 GGUF SHA와 종류를 확인한다. `/api/show`의 template과 같은 입력의 raw/일반 생성 결과·토큰 수를 대조한다. 각 모델 종료 시 VRAM 반환을 확인한다.
4. 한국어 이어쓰기와 기존 6개 입력의 결과를 수집하고, 프로세스를 종료해 포트 해제를 확인한다. 새 프로세스에서 같은 요청을 다시 보내 최초·재로딩·NFS 출력과 같은지 형식별로 기록한다. 기본 실행과 기존 성능 시험의 커널·배치 설정이 다를 경우 이를 구분해 비교한다.

## 후속 품질 시험안

| 질문 | 입력·통제 | 기록할 결과 |
|---|---|---|
| 양자화가 다음 토큰 예측을 얼마나 바꾸나 | 위 holdout·tokenizer와 문서별 창, stride, BOS/EOS 및 마스크를 재사용한다. 동일 표적의 총 NLL과 토큰 수로 언어별 PPL을 계산한다. | BF16 대 native 허용 상승 1%, Q8 대 BF16 1%, Q4 대 BF16 5%인 기존 기준을 재사용한다. 언어별 결과와 문서 단위 paired bootstrap 구간을 구분한다. |
| 한국어 이어쓰기와 영어에서 어떤 오류가 늘어나나 | 기존 6개는 회귀용으로 유지한다. 별도 비공개 평가 입력을 언어·길이별로 구성하고 모델명과 순서를 가린다. | 의미 연결, 사실 오류, 조기 EOS, 반복 4-gram 비율과 대표 원문을 기록한다. 사람 평가에는 문항 수와 일치율을 함께 표시한다. |
| 산수·코드 오류가 양자화에 따라 달라지나 | 산수는 단위·숫자 표현 정규화 후 정답 비교. 코드는 난수 seed가 고정된 작은 함수로 구성하고 AST 구문 검사와 준비된 단위 사례로 검증한다. 생성 코드 실행은 네트워크·비밀·프로젝트 쓰기 권한이 없는 제한 환경에서 수행한다. | 전체와 난도별 분자/분모, 정답률, 구문 오류율, 단위 사례 통과율을 적는다. 기존 한국어 분배 문제의 오답, Q4 짝수 판정 반전과 인자 수 오류도 추적한다. |
| Base 모델이 객관식 지식을 구분하나 | KoBEST·KMMLU 등을 후보로 검토한다. 실제 사용할 split·revision·허용 용도와 prompt 포맷을 기록한다. 선택지는 가능한 경우 답안별 조건부 로그우도로 채점한다. | 문항 수, zero/few-shot 조건, 길이 정규화 방식, 정확도와 BF16 대비 일치·불일치 수를 공개한다. 미실행 benchmark 점수는 카드에 넣지 않는다. |

각 회차에서 seed, 실제 prompt 바이트·토큰 ID, 생성 설정, 응답과 종료 이유를 저장한다. 형식이 다른 출력은 정답과 의미를 따로 채점한다. BF16 출력과 글자가 다르다는 이유만으로 오답을 부여하지 않는다.

## 후속 성능 시험안

- 단일 사용자 지연은 입력 128·1,024·3,072토큰 × 출력 상한 32·128토큰으로 비교한다. 최대 문맥은 입력과 출력을 합쳐 4,096토큰 안에서 시험한다. 예를 들어 입력 3,968 + 출력 상한 128을 사용하고, 정확히 4,096토큰인 순방향 채점은 생성과 따로 측정한다.
- 부하 시험은 동시 요청 1부터 시작해 2·4로 늘린다. 현재 Ollama 기준선은 `OLLAMA_NUM_PARALLEL=1`이다. 큐 대기를 포함한 응답시간과 실제 병렬 처리를 구분하고, 서버 동시성을 바꾼 시험은 별도 행으로 남긴다.
- 최초 다운로드, 캐시가 있는 상태의 서버 시작, 최초 모델 로딩, warm 요청을 분리한다. 준비 요청 후 조건별 최소 5회를 교차 순서로 반복해 중앙값과 범위를 구한다. p95는 표본 수를 함께 표시하고, 충분한 반복을 확보한 조건에서만 해석한다.
- 스트리밍에서 첫 생성 토큰까지의 시간(TTFT), 출력 토큰당 지연, 생성 토큰/초, 전체 요청/초, OOM·오류율을 측정한다. 0.5초 간격 GPU별 VRAM, 동시에 관측한 합산 VRAM, 서버 트리 PSS와 컨테이너 파일 캐시를 기록한다. 측정 가능한 경우 prefill/decode를 분리한다.
- 같은 H200에서 각 형식을 단독 시험하고, 운영 조합은 GPU 0 vLLM과 GPU 1 Ollama로 다시 측정한다. 기존 순차·병행 각 2회 실측은 평균 174.99초와 117.45초였다. 최대 동시 합산 VRAM은 9.20 GiB와 13.44 GiB였다.

## 게시할 설정과 사용자가 선택할 설정

| 위치 | 이번 값 | 나중에 변경하거나 기록할 때 |
|---|---|---|
| Hugging Face 저장소 | `pathcosmos/JANSORI`, 공개 모델, 가중치·토크나이저 Apache-2.0 | README YAML에 `library_name: transformers`, `pipeline_tag: text-generation`, `language: [ko, en]`, `license: apache-2.0`, `tags: [gguf]`를 유지한다. 평가 표에 실행한 모델 커밋, GPU·런타임 버전, 평가셋 revision을 적는다. |
| HF 가중치와 tokenizer | LlamaForCausalLM, 28층, hidden 3,072, Q head 24/KV head 8, vocab 65,536, 최대 문맥 4,096, BF16, BOS/EOS/PAD 0/1/2 | 이 값은 체크포인트의 구조다. 처리량 조절은 서버 설정에서 한다. 구조·토크나이저를 바꾸는 릴리스는 기존 수치 검증을 다시 수행한다. |
| GGUF | 동일 고정밀 원본에서 각각 직접 생성한 Q8_0, Q4_K_M. 한 종류당 루트 GGUF 파일 하나. `template` 파일의 본문은 `{{ .Prompt }}`. | 파일 이름으로 태그가 구분돼야 한다. 각 릴리스에서 원본 계보, 텐서 타입, 파일 크기·SHA와 registry 태그 매핑을 갱신한다. |
| vLLM 재현 요청 | `/v1/completions`, `max_tokens=64`, `temperature=0`, `seed=47000`. 단일 GPU. | 순차·병행 기준선은 `--max-num-seqs 2 --max-num-batched-tokens 4096 --gpu-memory-utilization 0.25 --num-gpu-blocks-override 512 --enforce-eager --no-enable-prefix-caching --generation-config vllm --seed 47000`을 사용했다. 간단한 사용자 명령과 이 측정 설정을 카드에 구분한다. |
| Ollama 재현 요청 | `/api/generate`의 `options`에 `num_ctx=4096, num_predict=64, temperature=0, repeat_penalty=1, seed=47000, num_gpu=99, num_thread=4`. `num_gpu`는 GPU에 올릴 레이어 수 설정이다. | `CUDA_VISIBLE_DEVICES`로 물리 GPU를 고른다. chat 시스템 문구를 추가하면 입력이 바뀐다. 창작용 샘플링은 별도 프로필로 시험한 뒤 권장값을 정한다. |
| 파일과 재현성 | `SHA256SUMS`, 4개 BF16 shard와 index, tokenizer·설정, 두 GGUF, LICENSE·NOTICE·모델 카드 | README만 수정하더라도 SHA256SUMS의 README 해시와 새 commit을 함께 갱신한다. 카드의 평가가 어느 가중치와 실행 설정에서 나온 것인지 명시한다. 문서 변경과 가중치 변경을 릴리스 기록에 구분한다. |

HF의 모델 카드 메타데이터, Ollama 양자화 태그와 루트 template, vLLM 서버 옵션 및 Ollama 생성 API 세부사항은 공식 문서를 참조한다. [Hugging Face 모델 카드](https://huggingface.co/docs/hub/model-cards) · [Hub의 Ollama 지원](https://huggingface.co/docs/hub/ollama) · [vLLM 0.11.2 옵션](https://docs.vllm.ai/en/v0.11.2/configuration/engine_args/) · [Ollama 생성 API](https://docs.ollama.com/api/generate)

### 실제 사용자 명령의 출력 길이

배포본의 Q8_0에서 모델 카드의 `ollama run hf.co/pathcosmos/JANSORI:Q8_0 "한국의 수도는"`은 생성 중 18,000토큰을 넘겼고 사용자가 Ctrl+C를 누르는 방식으로 종료했다. 문맥 창은 처리하는 입력 이력의 길이를 정한다. 이 모델과 Ollama에서는 창을 이동시키며 출력을 계속 생성할 수 있다. 짧은 예제를 재현하려면 생성 상한을 따로 지정한다.

```bash
curl -s http://127.0.0.1:11434/api/generate -H 'Content-Type: application/json' -d '{
  "model": "hf.co/pathcosmos/JANSORI:Q8_0",
  "prompt": "한국의 수도는",
  "stream": false,
  "options": {"num_ctx": 4096, "num_predict": 32, "temperature": 0, "repeat_penalty": 1, "seed": 47000}
}'
```

Q4는 위 모델명의 태그만 `Q4_K_M`으로 바꾼다. 나중에 기본 CLI 출력에도 상한을 적용하려면 Hub가 지원하는 루트 `params` 파일에 `num_predict`를 넣은 후보를 별도 revision으로 시험하고, 새 Ollama 저장소로 다시 내려받아 CLI가 해당 설정으로 종료되는지 확인한다. 현재 릴리스의 18개 파일에는 이 후보가 포함되지 않는다.

최초·재로딩의 32토큰 greedy 요청에서 Q8_0과 Q4_K_M은 모두 “한국의 수도는 베이징이다”라는 잘못된 이어쓰기를 생성했다. 동일 커밋·설정에서 재현되어도 사실이 맞는지는 별도 검사가 필요하다. 후속 사실 평가에서는 수도 한 문항을 튜닝 기준으로 쓰지 않고 독립 문항과 정답집을 사용한다.

## 이 배포 commit으로 확인된 설정 차이

| 실행 | 한국어·영어·수학·코드 고정 입력 6개 | 최초 실행과 재로딩 |
|---|---|---|
| vLLM 카드 기본값 | NFS 기준과 4/6 일치. 영어 이야기와 방정식 출력이 달랐다. | 6/6 일치 |
| vLLM, NFS와 같은 eager·attention·배치·캐시·생성 옵션 | NFS 기준과 6/6 일치 | 별도 설정 대조 1회 |
| Ollama Q8_0, Q4_K_M | 각각 NFS 기준과 6/6 일치 | 각각 6/6 일치 |

GPU 0의 vLLM 기본 실행에서 요청 직후 `nvidia-smi` 관측값은 130,149 MiB였다. 기준선 표의 BF16 8,805.9 MiB는 KV 캐시 예산 등을 줄인 별도 설정에서의 값이다. 가중치 6.45 GB만으로 서버의 전체 VRAM을 산정하면 안 된다. Ollama 0.40.16에서 Hub 경유 모델의 `/api/ps`는 양자화 종류를 `unknown`으로 표시했다. `/api/show`의 Q8_0·Q4_K_M 표시, `/api/ps`의 실행 중 manifest digest, 실제 GGUF blob 해시를 함께 대조해 태그와 장치 메모리를 검증했다. 두 API의 역할을 구분해 기록한다. [공개 배포 시험 결과](RELEASE_REPORT.md)
