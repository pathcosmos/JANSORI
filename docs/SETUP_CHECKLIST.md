# 새 기기 세팅 체크리스트 (저장소/데이터가 초기화된 상태를 가정)

## 2026-11-16 현재47K 인계 점검

- [x] 47,000 최종 체크포인트 채택·전체SHA·네구간 장부·source 종료
- [x] 최종 native 한국어/영어 평가·고정 생성·품질 위험 기록
- [x] 실제8GPU 일반 전체상태 복원·연속2/분할1+1 exact 비교
- [x] 부모19곳 접근 차단 실제8GPU 독립 복원·원권한/전체SHA 복구
- [x] 정상NFS3·로컬15·보호부모3 보존 감사
- [x] 최종 NFS manifest·프로세스 정리·완료 인계 인증
- [x] 새 H200 서버에서 47K 인계 전수 SHA, 격리 환경 설치와 GPU 0의 작은 모델 BF16·Q8_0·Q4_K_M 실제 생성·재로딩 검증 완료
- [ ] 다른H2001GPU에서 BF16·Q8_0·Q4_K_M 가공 및 vLLM/Ollama 실제 검증

새1GPU 후처리는 `docs/GOAL_1GPU_POSTPROCESSING_47000.md`의 단일GPU 진입 계약을 따른다. 아래 원래 개발/학습 환경의8GPU 검사는 해당 학습 작업에 적용한다.


## 원본·중간본 보존 검토 이후의 복구 경로 (2026-11-07)

- 현재 최종 tokens/tokenizer/mix·dedup 원문·코드 정제본·split/reader DB·내용 증명이 보존돼 있다면, raw/중복제거 전 clean 누락만으로 전체 다운로드부터 다시 시작하지 않는다. 학습·native 평가가 실제 사용하는 보존 대상을 먼저 확인한다.
- 원본727파일394.609GB + 중간 정제662파일273.118GB와 추가 후보5개6.131GB를 사용자 승인 후 **합계1,394개 삭제 완료**, NFS 여유 증가629.931GiB를 확인했다. 최종 입력·복구 증거는 보존했다. 원격 고정 revision/SHA 전수 대조,6파일4.67GB 재다운로드,6소스4096행 정제 재현 및 후보 접근차단 상태의 receipt 검증을 통과했다. 전체 재생성 완료 증거와는 구별한다.
- 재다운로드·재생성이 필요하면 [DATA_RETENTION_AND_RECOVERY](DATA_RETENTION_AND_RECOVERY.md)를 우선한다. 영어56개·코드spread64개·고정 revision·OWM math 필터·dedup source 순서를 보존한다. 기존 `.done`과 충돌하지 않도록 새 복구 루트를 사용한다.
- 코드와 함께 `docs/recovery/data_retention_v1/`의 복구 묶음·원격 SHA 목록·후보 내용 증명을 보존한다. 아래 과거 체크리스트의 raw 누락은 현재 최종 학습 입력의 누락과 같은 뜻이 아니다. 기존 `preflight.py`의 raw/clean 항목은 optional 점검이며 실제 보존 상태는 최신 STATUS/삭제 receipt로 확인한다.

> 원칙: **git에는 코드·문서·MANIFEST만 있다.** 데이터(raw/clean/tokens)와 체크포인트는 git에 없다.
> agent는 `python scripts/preflight.py`를 먼저 돌려 무엇이 없는지 확인하고, 아래 단계를 위에서부터 수행한다.
> 각 단계 끝에 `docs/STATUS.md`를 갱신한다. 시간/용량 추정은 원 개발 박스(12코어) 기준이며 새 기기는 더 빠를 것으로 예상.

## A. 저장소와 환경
- [x] 현재기기 hardware_profile/단계별실행기·local/NFS분리기준 검증. 새기기·새장시간작업은 하드웨어를 다시 측정하고 [HARDWARE_RESOURCES](HARDWARE_RESOURCES.md)를 적용
- [ ] `git clone <private repo url>` (repo: pathcosmos 계정의 private `llm-traditional`; gh auth 또는 PAT 필요) → 프로젝트 루트로 이동
- [ ] Python 3.11+ , CUDA 드라이버 확인 (`nvidia-smi` 에 H200×8)
- [ ] `bash scripts/setup_env.sh` (requirements 설치 + 환경 리포트). torch 2.7.0+cu126 에서 검증됨. 더 새 버전이면 8GPU 스모크로 재검증
- [ ] `python scripts/preflight.py` → REQUIRED missing 이 없을 때까지. GPU가 8장 미만이면 **중단하고 사용자에게 보고**
- [ ] **cgroup memory.max / CPU 코어수 기록**을 STATUS.md에 남기고 정제 워커 수 결정 (과거 사고: 워커 10개×25GB > 240GB → OOM → 풀 정지, docs/LESSONS.md 1번)
- [ ] HF 접속 확인: `curl -s -o /dev/null -w "%{http_code}" https://huggingface.co` = 200 (공개 데이터셋만 사용, 토큰 불필요). 다운로드 가속: `hf_transfer`/xet 설치됨

## B. 데이터 확보 — 두 경로 중 택1 (사용자에게 어느 경로인지 확인)
### 경로 1: 이전 기기에서 `data/clean/` + `tokenizer/` + `MANIFEST.json` 을 받은 경우
- [ ] `python scripts/make_manifest.py --verify` → "mismatches: none". 불일치 파일은 재전송 또는 B-경로2로 재생성
- [ ] raw가 필요한 작업(HPLT↔FW2 URL 겹침 측정 등)만 raw 일부를 재다운로드
### 경로 2: 아무 데이터도 없는 경우 (전부 재생성)
순서/예상(원 박스 기준). 모두 재시작 가능(.done 마커). 정확한 샤드 수는 preflight 의 기대값과 같다.
| 단계 | 명령 | 기대 결과 | 비고 |
|---|---|---|---|
| 1 | `python data/download.py fineweb2_ko` | raw/fineweb2_ko 25 parquet, ~99GB | HF `HuggingFaceFW/fineweb-2` `data/kor_Hang/train/*`. 수 분 |
| 2 | `python data/download.py wiki_ko` | 3 parquet 0.75GB | `wikimedia/wikipedia` `20231101.ko` |
| 3 | `python data/download.py hplt_ko` | 464 parquet ~114GB | `HPLT/HPLT2.0_cleaned` `kor_Hang/*` |
| 4 | `python data/download.py fineweb_edu 56` | 56 parquet ~113GB | `sample/100BT` 앞 56개 파일 |
| 5 | `python data/download.py owm` | 114 parquet ~26GB | `open-web-math/open-web-math` |
| 6 | `python data/clean.py fineweb2_ko ko <W>` | 25 shards, 5866만 문서 유지(96.4%), 975억 자 | 원 박스 약 4시간. 샤드당 RAM ~10-25GB → W 제한 |
| 7 | `python data/clean.py wiki_ko ko 2` | 45.5만 문서(70%), 0.6억 자 | ~4분 |
| 8 | HPLT 겹침 측정 → `python data/clean.py hplt_ko ko <W>` | | raw URL 비교와 텍스트 유사중복 판단은 구분. RUNBOOK 참조 |
| 9 | `python data/clean.py fineweb_edu en <W>`; `python data/clean.py owm math <W>` | | OWM 수학 전용 필터 사용 |
| 10 | 샤드/소스 간 정확중복 제거 | | `data/dedup.py`, 최종 본문 NFC SHA256, 별도 exact_v1 출력. RUNBOOK 참조 |

## C. 토크나이저 / 토큰화 / 믹스
- [x] 별도 병렬토큰화·완료분포 도구 준비: 6집중tests/기존v3바이트동일/worker중단재개/512MiB13설정·BPE/RAM실측완료. 실제전체F4/F5/F6통과와구분,STATUS·next_stage_optimization_v1참조
- [x] F1 중 독립 준비3건: 지문 join·후보 누락 완전 비교·100만 split runner 보완, CPU89개/작은 실제·합성 증거(STATUS 참조). 전체 데이터 품질 완료와 구분
- [x] 감사 종료 후100만 synthetic split 실제 측정:cache64/512 비교·100만행논리결과동일,105tests. 300만확대완료,local/NFS100만발행·강제종료재개·논리대조완료(112tests+최종집중4tests)
- [x] F0 파일별 재개 감사·내용 증명 연결·새 job30초 감시 준비: CPU81개 및 실제6파일 pilot 통과. 전체726파일 감사는 실행 중이며 완료 체크는 다음 항목의 실제 결과로 판단
- [x] F1 전체 내용 감사 통과/726파일·128,856,258행·source SHA/provenance 확인 (runs/source_audit_full_v1, 2026-11-06 08:27 UTC 완료). 후속검토에서 지문726파일 존재/footer행수 및 보고서완료증명 대조. 품질/ready는 다음항목
- [x] [DATA_CONTRACT](DATA_CONTRACT.md)에 따라 범위를 명시한 품질·holdout/byte정책 확정 및 audited-ready 생성/consumer검증 완료(2026-11-06 09:28 UTC). data/datasets/final_v1.ready.json. 실제전체split/tokenizer는 후속
- [x] 토크나이저 학습 전 실제 전체 그룹 분리·최종 NFS 저장/consumer 검증 완료:726파일·128,856,258문서,train127,565,418/eval1,290,840. runs/f4_full_v2 report/.done·관측기·분포검토 연결(2026-11-06 18:16 UTC). 최종 tokenizer/토큰화는 다음 항목의 실제 결과로 판단
- [x] 실제 train-only699,097문서·약3GB에서 raw65536학습완료: tokenizer/candidate_v1/tokenizer.json, runs/f5_tokenizer_v1 보고서/.done(2026-11-06). 원본 tokenizer 보존
- [x] 최종raw65536·원문복원/특수토큰/ID·독립6144문서/4범주효율검사통과. runs/f5_tokenizer_v1/tokenizer_check.json·efficiency.json·완료증명 대조; 상용모델 능력평가와구분
- [x] 실제 최종 산출물 `data/tokens/final_v1` 전수726파일/한영50M 검증 완료(2026-11-06, F6완료증명 참조). 재생성 명령 예: `python data/tokenize_shards.py <src> [holdout_docs] --sources-manifest <ready.json> --tokenizer <검증한 후보> --output-dir data/tokens/v2/<src>` — 최종 분리 정책에 맞게 수행. 한/영 각 약 50M tokens는 실제 토큰 수로 확인
- [x] 실제 최종 `data/tokens/final_v1/mix.json` 발행·내용검증 완료. 재생성 명령 예: `python data/build_mix.py <src:weight> ... --token-root data/tokens/v2 --output data/tokens/v2/mix.json` → 완료한 동일 tokenizer 산출물만 포함. 실제 토큰량으로 비율 확정
- [x] 토큰 합계/예상 반복 노출량을 STATUS.md에 기록. sampler는 복원추출이며 엄밀한 epoch와 다름

## D. 학습 검증 (본학습 전 필수)
- [x] 새 본학습 NFS10+로컬 과거15 보존 구현·CPU 장애/실제AdamW 재개·실제3B1,020tensor/8rank RNG CPU복원 검증. 운영 적용·25개전체크기/장기8GPU 검증과 구분한다. 정확한 경로·미승인 계약·공간·복구는 [CHECKPOINT_RETENTION](CHECKPOINT_RETENTION.md). 실제 F8/최종평가/F9 gate와 본학습·운영정리 확인은 아래 별도 미완료 항목을 따른다.
- [x] 실제 최종-tokenizer pilot의1B micro8/3B micro4·8GPU·4096/global2M deterministic14case finite/uneven-eval/model+optimizer/RNG/save/resume/변경입력·NaN 검증 완료. runs/actual_train_validation_v1 report/.done(2026-11-06); native PPL·전체 mix·10B 리허설은 아래 별도 gate
- [ ] 새 환경에서 `scripts/validate_distributed.py --run-dir runs/c2_<새이름>`으로 작은 8GPU 계약·재개·불균등 평가·비유한 수치 검증. 현재 서버의 2026-11-06 합성 suite 통과 기록은 STATUS 참조. 실제 데이터/compile 검증은 별도
- [ ] 8GPU 스모크: `torchrun --standalone --nproc_per_node=8 train/train.py --config train/configs/smoke.json` (먼저 `python scripts/make_smoke_data.py` 로 /tmp/smoke_tokens 가짜 데이터 생성). 확인: 초기 loss ≈ ln(65536)=11.09, loss 감소, ckpt 저장/재개, 8랭크 샤딩 정상
- [ ] `compile=true` 로 1b/3b MFU 측정 (목표 35%+), micro_bsz 튜닝, OOM 여유 확인
- [x] 실제1B 5,000step/10.48576Btokens 리허설·최종native평가·곡선/생성 검토 완료. runs/1b_rehearsal_final_v3 및 MAIN_TRAINING_READINESS 참조. 생성 품질 위험은 남아 있으며 본학습 승인과 구분한다.
- [x] 2026-11-07 사용자 승인 기록 후 검증한 `runs/3b_full_v1/main.json`으로3B 본학습 감독기 기동. 초기 실제 학습·전체57,000step/최종 저장·복원·평가는 진행 상태를 STATUS에서 확인한다. 기존 `train/configs/3b.json` 초안은 사용하지 않았다.
- [ ] 최신 승인된3B 누적47,000step 조기 종료·최종 저장/SHA·native 평가·일반/부모 독립8GPU 복원·보존 감사·NFS 인계 완료. 기존50K LR 계산식을47K까지 사용한다. HF BF16·GGUF Q8_0/Q4_K_M 및 vLLM/Ollama 실제 검증은 다른1GPU 기기의 별도 Goal로 인계한다. 실제 상태는 STATUS와 GOAL_8GPU_TRAINING_HANDOFF를 따른다.

- [x] N10 평가 입력: `eval/prepare_holdout.py`로 실제 split의 ko/en eval 문서만 추출하고 `eval/run.py --holdout-manifest <추출/manifest.json>`으로 증명 확인. 수동 입력의 성공을 verified holdout으로 간주하지 않음

## E. 남은 구현·검증
KMMLU·KoBEST·HAE-RAE 등 평가 task 연결, 실제 후보 tokenizer 정책/품질·최종 holdout PPL/생성 실행, 실제 데이터/본학습 크기 분산 검증, 대량 그룹 분리, `sft/`. tokenizer 검사 CLI·native 평가 CLI/metadata·micro compile/eager 재개 검증·그룹 split 토큰화는 구현/합성 검증됐으며 실제 상태는 STATUS 참조.
