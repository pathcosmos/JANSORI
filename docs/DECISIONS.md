# 결정과 근거

- **2026-11-07 비용 우선순위:** 사용자 명시 지시로 외부 저장·전송은 업로드/다운로드 비용 무료·최소화 → 저장 용량 비용 → 속도 순. 전송 관련 API/조회/중계 비용까지 비교한다. R2 Standard/Storage Box를 우선 비교하고 B2는 예상 사용량이 무료 범위에 드는 조건부 대안. 무결성·재개·보존 조건 유지, 속도만으로 유료 전송안을 우선하지 않는다. 과거 B2 우선안은 이전 판단으로 보존한다.

- **2026-11-06 16:51 UTC 최종tokenizer 효율검사 기준 사전명시:** 독립된전체eval문서(source당1024개)에서고정실험64k baseline학습그룹을제외하고raw/special/uint16검사와동일텍스트효율을비교한다. 범주token-count비율≤1.10,승인byte정책가중tokens/byte비율≤1.02를공학적초기guardrail로정했다. 성능/범용품질보장아님. 초과는needs_review로기록하고근거없이사후기준완화하지않음. 근거runs/f5_tokenizer_v1/contract.json.
- **Dense, MoE 아님**: 사용자 요구.
- **직접 구현(순수 PyTorch+FSDP2)**: 사용자가 "작성에 의미". torchtitan/Megatron 미사용. 3B는 TP/PP 없이 FSDP만으로 충분.
- **3B / ~120B tokens**: 8×H200, 본학습 ~10일 예산에 맞춤. 폴백 2B/80B.
- **토크나이저 직접 학습**: 기존(Qwen/Llama)은 한국어 1.46~1.62 chars/token, 직접 학습은 2.43 → 같은 연산으로 한국어 ~50%+ 더 학습.
- **WSD 스케줄**: 중간 중단/연장이 쉬워 일정 리스크 관리에 유리.
- **정제는 직접 작성**(datatrove 미사용): 학습 목적. 순수 Python+multiprocessing, 샤드 단위 재시작.
- **CulturaX/Stack v2 제외**: gated(승인 필요). 현재 코드는 codeparrot/github-code 선택64파일을 확보·정제했으며 실제 최종 구성/교차검사 범위는 후속 품질 판단을 따른다.
- **FineWeb-2는 이미 정제된 데이터**라 필터는 보수적(96.4% 통과). 한글 비율 ≥0.30, 길이 200~100k자, 줄중복>30%, 5-gram 반복>30%, 영숫자비율<0.5, 이메일/전화/주민번호 마스킹.
- **문서 경계 어텐션 마스킹 안 함**(packing, 표준 단순화).
- **전처리는 이 박스에서 끝내지 않고 8×H200 환경(CPU 비례 증가)으로 일찍 이동**: CPU 12코어가 병목이라 새 환경이 훨씬 빠름. 이동 전 FineWeb-2 정제와 문서화만 마무리.

- **2026-11-06 / N5 새 tokenizer의 정규화는 raw**: 코드 literal의 `e + combining acute`를 NFC로 바꾸면 문자열 코드포인트·바이트/길이가 달라진다. REF-DATA의 코드 보존을 tokenizer까지 유지한다. 학습기 기본 `--normalization raw`, NFC는 명시적 비교/호환 옵션으로 남긴다. 검사기는 `--normalization raw`로 normalizer 없음·전체 raw roundtrip을 확인한다. 웹 중복 판정/그룹 연결의 NFC는 그대로 유지한다.
  - 근거: runs/normalization_v1의 고정 실제 소량 표본(학습 384건/평가 128건, vocab 2048)에서 4범주 토큰 수는 동일. 표본에서는 NFC 차이를 관측하지 못했으며, 별도 Unicode 코드 probe에서만 NFC raw 변화 1건/raw 방식 0건. 대표성·전체 효율을 입증한 실험은 아니다. 최종 65536 후보의 분리 표본·실제 효율 검증은 여전히 필수.

- **2026-11-06 benchmark는 별도 작업**: 사용자 선택. 현재 감사/병행1·2·3을 유지하고 benchmark 실행은 시작하지 않는다. 데이터 품질 확정 시 오염검사 범위, 실제 모델 평가 진입 시 benchmark 준비 시점을 대화 중 알린다.

- 2026-11-06 사용자 추가 지시: GPU 벤치마크 실행 및 최적화 후보 검토를 승인했다. 합성 bf16 수치 확인→tiny→1B/3B compile·micro batch 비교를 진행한다. 공식 언어능력 task는 tokenizer/학습 checkpoint 준비 후 별도이며, 실제 source 감사와 본학습은 구분한다. 실행 결과는 STATUS 최상단과 runs/gpu_benchmark_v3를 따른다.

- **2026-11-06 사용자자원지시:** 향후전체작업을이기기실측자원에맞춰효율화. CPU96/메모리1920GiB/H2008장/NFS+로컬별도공간확인. local1M은NFS보다4.75배빠르고100만행논리동일. 단계별thread/worker예산·local임시공간·GPU병렬구성을HARDWARE_RESOURCES에고정,진행중비교설정과기존본학습/삭제/업로드확인규칙유지.

- **2026-11-06 총완료시간최우선:** 사용자명시지시. 작은fixture의4GiB/RSS·512MiBcache를대규모기본값으로승계하지않고, 큰working set의RAM활용·병렬읽기/계산을실측해기동한다. 사용률자체가목표가아니며동일검증의전체elapsed로판단. SHA32worker/중복해시pass제거,cache/SQL반복측정은STATUS의실제결과를따른다.
