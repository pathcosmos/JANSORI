# JANSORI 47K 배포 및 다운로드 검증

사용자 승인에 따라 세 형식을 [단일 공개 저장소의 같은 커밋](https://huggingface.co/pathcosmos/JANSORI/tree/e68bfa3cb31e9589f4481a2e31d6d75c731fec9d)에 게시했다. 가중치와 토크나이저에는 Apache-2.0을 적용했다. 배포 commit은 `e68bfa3cb31e9589f4481a2e31d6d75c731fec9d`이다. 비로그인으로 18개 파일 11,843,985,632바이트를 확인했다.

## 계보 및 보존

NFS 배포 경로는 `/home/work/llm-dense/llm-traditional/runs/3b_47k_hub_release_v1/nfs-release-v1`이며 manifest SHA-256은 `383b5d8aa9d70e8c087a7a8f74d012e08d4975ae4a71517d5a643d7b757ae73c`다. 원본은 `runs/3b_full_50k_v1/training/ckpt_0047000`이다. BF16 패키지 manifest SHA-256은 `44a0eb5d3f4f52761c652f72454976462b0996866c3f50bb3470c8c4e7b6e09c`이다. 이 패키지에서 만든 공통 고정밀 GGUF SHA-256은 `c69c8a77121940e0c1f06f668ba0a80e57edba1afaac777cae3ea3f923d69b7b`이다.

- Q8_0 SHA-256 `eb7053112886dab60d6012d4635dc2c7cdd07c902317e202a91ba36da33fed41`
- Q4_K_M SHA-256 `21f8c90ade582967a8acb1991f73bea5f62757ae2d430500da41ba458f0f52a0`

## 형식별 실측 및 다운로드 검증

| 형식 | GB | 한국어 / 영어 PPL | 로딩 초 | 생성 토큰/초 | GPU 최고 MiB | 기본 실행 NFS 일치, 최초 / 재로딩 |
|---|---:|---|---:|---:|---:|---|
| BF16 | 6.448 | 9.08981 / 10.21574 | 87.10 | 100.27 | 8805.9 | 4/6 · 4/6 |
| Q8_0 | 3.426 | 9.09086 / 10.21729 | 23.65 | 14.49 | 5055.9 | 6/6 · 6/6 |
| Q4_K_M | 1.970 | 9.19630 / 10.31774 | 23.53 | 9.95 | 3761.9 | 6/6 · 6/6 |

형식별 성능은 첫 순차 회차의 로컬 H200 실측이다. 두 순차 회차 평균은 174.99초, 두 병행 회차 평균은 117.45초였다. 서버 트리 최대 PSS는 순차 3.64 GiB, 병행 4.69 GiB였다. 동시에 측정한 두 GPU 합산 VRAM 최고치는 순차 9.20 GiB, 병행 13.44 GiB였다. 고정 입력을 새 프로세스에서 다시 실행해 각 형식에서 최초·재로딩 출력 6/6 일치와 정상 종료·포트 해제를 확인했다.

새 HF 캐시와 Ollama 모델 저장소에서 공개 배포본을 받았다. 일부 HF 전송이 지연되어 같은 commit의 HTTP Range로 BF16 shard의 67,104,768바이트를 보완하고, 새 Ollama 저장소에서 받은 Q8 파일을 재사용했다. 18개 파일 각각의 크기와 SHA-256을 검증했다. GGUF 태그 manifest, 받은 blob, NFS 양자화 파일의 SHA-256이 일치했다.

## 실행 및 재현

공개 다운로드 검증에 사용한 사용자 명령이다. 각 서버에 GPU 한 장을 노출한다. Ollama CLI의 긴 출력을 중단하려면 Ctrl+C를 사용한다. 출력 상한이 필요한 경우 설정 문서의 API 예제를 쓴다.

```bash
REV=e68bfa3cb31e9589f4481a2e31d6d75c731fec9d
CUDA_VISIBLE_DEVICES=0 vllm serve pathcosmos/JANSORI --revision "$REV" --tokenizer-revision "$REV" --load-format safetensors --dtype bfloat16 --tensor-parallel-size 1 --max-model-len 4096 --host 127.0.0.1
ollama run hf.co/pathcosmos/JANSORI:Q8_0 "한국의 수도는"
ollama run hf.co/pathcosmos/JANSORI:Q4_K_M "한국의 수도는"
```

[NFS 한 장 실행 및 재설치 절차](../nfs-release-v1/USAGE.md) · [수치·생성·성능 측정](../nfs-release-v1/REPORT.md) · [후속 상세 시험과 세부 설정](TEST_AND_SETTINGS.md) · [배포 검증 영수증](verified.json)

## vLLM 실행 설정에 따른 출력 차이

기본 모델 카드 명령에서는 영어 이야기와 방정식 프롬프트의 출력이 기존 NFS 측정과 달랐다. 기본 명령을 재기동하면 최초 실행의 6개 출력이 모두 재현됐다. 같은 공개 commit에서 기존 NFS 벤치마크의 eager·attention·배치·KV 캐시·생성 옵션을 함께 적용하면 6개 모두 NFS 측정과 일치했다. 이 대조만으로 개별 옵션 하나를 원인으로 특정할 수 없다. [기본 실행](vllm-1/BF16-generation.json) · [기존 설정 대조](vllm-3/BF16-generation.json)
