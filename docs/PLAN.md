## 최신 승인 목표 복원 (2026-11-09)

사용자가 누적57,000스텝으로 복원하고10,000스텝 인증경계에서 전환하도록 승인했다. `runs/3b_57k_transition_v2/plan.json`에 따른 대기기기동 완료이며 기존35k생산자는 실제전환 전까지 유지된다. 원8k→현재10k→새57k 세 계약의 기록을 합산하고, 보호사본·정지·스냅샷정리·실제8GPU 비교가 통과한 뒤에만 `runs/3b_full_57k_v2`를 기동한다. 실제전환 완료는 최신STATUS와실행증거로 확인한다. 최종저장/복원/native평가/HF동일성/vLLM실행/Ollama호환성 검토 범위는 유지하며 목표를57k로 승계한다. 상세는 [57k 복원 실행계획](MAIN_TRAINING_57K_RESTORE_PLAN.md). 아래35k문구는 과거계획이다.

## 최신 목표 개정 (2026-11-08)

사용자 최신 목표는35,000step + 최종 저장·실제복원·native평가·실행용 모델 패키징이다. 현재 실행 계약57,000은 아직 전환 전이다. [35k/추론 패키징 실행 계획](MAIN_TRAINING_35K_EXPORT_PLAN.md)의 단계1~5와 실제 증거를 따른다. 감쇠29,750~35,000, vLLM 우선·Ollama 호환성 검증, SFT 자동진행 없음. 이전40k 준비본/57k·F10 일정은 과거 참고이며 최신 목표를 대체하지 않는다.

# 전체 계획 (확정본)

## 본학습 보존 확대 준비: NFS10 + 로컬 과거15 (2026-11-07)

사용자 요청으로 새 `runs/3b_full_v1`의 NFS 최신10개와 휘발성 로컬 과거15개 보존을 구현·검증했다. 아래 과거 최신2개 제안을 이 새 검토안으로 확장한다. 운영 적용/정리/본학습 승인은 아직 없다. native `keep_ckpts=57001`로 자동삭제를 끄고, 새 감독기의 검증된 로컬 수신 ACK 후에만 해당 run의 오래된 NFS 사본을 정리한다. 재개는 NFS만 사용한다. 실제 CPU 장애·AdamW 재개 시험과 실제3B1,020tensor CPU복원은 통과했으며, 장기8GPU/25개전체크기 운용과 구분한다. 경로·peak·복구·남은 gate는 [CHECKPOINT_RETENTION](CHECKPOINT_RETENTION.md)와 `tiered_retention_execution_draft_v2.json`을 따른다.

## 실제3B 선정값 기반 본학습 사전 제안 (2026-11-06T21:55:09.233325+00:00)

`runs/pretrain_readiness_v1/main_training_proposal_v1.json`은 아직 준비완료/실행승인이 아닌 초안이다. 현재 실제 검증 선정은 3B micro4·seq4096·world8·accum16/global2,097,152·eager/deterministic·LR3e-4이다. 기존 `train/configs/3b.json` micro8/compile-on은 과거 초안이므로 그대로 실행하지 않는다. max57,000=119,537,664,000tokens·warm1000/WSD15%를 검토한다.

실측138.49ktokens/s의 순수계산9.99일에 평가/저장/복구를 더한다. save/eval250step 제안은 약63.1분/각228회. 전체checkpoint보존은 약9.7TB라현NFS불가,새3B출력만 최신2개+작성중1개의 약118.84GiB 방안은 **별도정리허가가 필요한 미승인 제안**이다. 현재 리허설/진단 산출물은 보존한다. 실제1B5000/최종native검토·운영entry/감독/복구 검증·최종보고서가 완료된 뒤 본학습 시작 및 보존정책을 함께 제시한다.

## 실제 전체 token 수에 따른 mix 보정 (2026-11-06T21:17:47.745009+00:00)

train121,174,609,758/eval1,227,784,055tokens의 전수native계수에 따라 ko/en/code/math50/30/10/10을 유지한다. source weight는 wiki0.005/FW2 0.29464577095297395/HPLT0.20035422904702602/en0.3/code0.1/math0.1이다. 기존wiki5% 제안은119.537664B목표에서20.39회 반복이라0.5%(약2.04회)로 줄이고 한국어 웹 두source를 실제 train-token량에 비례시켰다. 원표의약45%웹/5%wiki는이전제안이며 현재선정은49.5%웹/0.5%wiki다.

`runs/f6_tokens_v1/mix_review.json`과 `scripts/mix_review_policy.py`에 원량·한영holdout50M·범주합·기대노출 guard/증명을 기록했다. source공급변화로 guard초과시멈추고검토하며 기존review를덮어쓰지않는다. 30초로컬관리기는 originalF6최종검증/actualnative평가/actualhook증명과 자원준입뒤 기존Goal의1B10.49B리허설로만연결한다. 전체본학습은별도확인.

## 단계별 다음 작업 최적화 선행 운영

단계 N 실행 중 N+1의 입력·기존 측정·병목을 확인하고 현재 job의 향후 peak/reserve 이후 여유에서 준비·측정한다. 검증/재개/출력 동일성과 총시간 개선을 확인해 다음 실행 계약에 적용한다. 실제 운영표는 `runs/pretrain_readiness_v1/stage_optimization_queue.json`, F6 전체파일/재사용 계획은 `runs/f6_preflight_v1/plan.json`을 따른다. F4/F5→F6(native shard/저장 예산)→F7(실제tokens8GPU)→F8(리허설 설정)→F9(시간·저장·복구 보고서) 순이며 각 단계 진입 조건과 본학습 확인 규칙을 유지한다.

## 목표/환경
- 한국어 중심 dense decoder LLM, **3B 파라미터, 약 100~120B 토큰**, 순수 PyTorch 직접 구현.
- 최종 학습 환경: **H200 ×8** (+ 비례해 많은 CPU/RAM). 전처리 개발은 H200 ×1 / 12코어 박스에서 시작했음.
- 총 기간 목표 20일 (본학습 약 10일). H200 8장 MFU 35% 가정 → 10일 ≈ 2.5e21 FLOPs ≈ 3B × ~120B tokens.
- **폴백**: 일정이 밀리거나 MFU가 낮으면 2B / 80B 토큰으로 축소. 먼저 1B로 ~10B 토큰 리허설.

## 모델 (model/model.py PRESETS)
Llama형: RMSNorm(pre), RoPE(theta 5e5), GQA, SwiGLU, untied embedding(3B), context 4096.
3B: 28 layers, dim 3072, 24 heads, 8 KV heads, ffn 8192, vocab 65536 → 3.22B params. 1B: 18 layers, dim 2048 (tied emb).

## 데이터 (목표 ~120B tokens)
| 구분 | 비율 | 소스 |
|---|---|---|
| 한국어 웹 | ~45% | FineWeb-2 kor_Hang·HPLT 2.0 ko(정제·정확중복 완료, 품질 확정 대기) |
| 한국어 고품질 | ~5% | 한국어 위키(정제·정확중복 완료) |
| 영어 | ~30% | FineWeb-Edu sample-100BT 선택56파일(정제·정확중복 완료) |
| 코드 | ~10% | codeparrot/github-code 선택64파일(정제·내부 exact 검사 완료, 교차 범위 결정 대기) |
| 수학 | ~10% | OpenWebMath114파일(정제·정확중복 완료) |
한국어 고유 토큰이 부족하므로 2~3 epoch 반복 허용. 정확한 비율은 정제 후 실제 토큰량을 보고 확정.

## 토크나이저
byte-level BPE, vocab 65536, 직접 학습(한국어 50 / 영어 35 / 코드·수학 15 by bytes), 숫자 한 자리씩 분리, 원문 Unicode 보존(normalizer 없음, 2026-11-06 N5 결정). 웹 중복 판정의 NFC는 별도 정책으로 유지. 특수토큰: <|bos|> <|eos|> <|pad|> <|im_start|> <|im_end|> + reserved 11.
시험판(한국어만, 200MB): 한국어 2.43 chars/token (Qwen2.5 1.46, Llama3 1.62), roundtrip 오류 0.

## 학습 설정 (train/train.py)
bf16 + FSDP2(블록별 fully_shard) + torch.compile, AdamW(0.9/0.95, wd 0.1, clip 1.0), peak LR ~3e-4(소형 모델로 확인),
**WSD 스케줄**(warmup→stable→마지막 15% 선형 decay), global batch ~2M tokens, seq 4096, ckpt 약 1시간 간격 + 자동 재개.
데이터 샘플러는 (seed, step, rank, micro)의 순수함수 → 로더 상태 저장 불필요, 정확한 재개.

## 일정(원안 20일)
1~4 데이터 / 3~5 코드·스모크·MFU 튜닝 / 6 1B 리허설 / 7~16 3B 본학습 / 17 평가 / 18~19 SFT / 20 버퍼·리포트

## 평가

2026-11-06 사용자 선택: 공식 벤치마크와 별도 GPU 성능 benchmark는 별도 진행. F2 오염검사 범위 결정 및 F7 실제 모델 평가 진입 전에 필요한 시점을 알린다. native 한/영 holdout PPL·고정 생성·분산 수치 검증은 기존 필수 경로를 유지하고 미실행 공식 task는 pending으로 기록한다.

KMMLU, KoBEST, HAE-RAE(한국어) + HellaSwag/ARC/MMLU(영어 보조), 한/영 holdout perplexity, 생성 샘플. SFT는 한국어 instruction 데이터.

## 참고자료 반영에 따른 실행 순서 보완 (2026-11-06)

모델 구조·목표 토큰·학습 프레임워크 결정은 유지한다. 상세 실행 조건은 [WORKFLOW](WORKFLOW.md)의 「참고자료를 실행 완료 조건으로 반영한 변경안」을 따른다.

- 2026-11-06 정확중복662/662 완료. 다음은 WORKFLOW 최상단 F0~F10에 따라 실제 내용 감사·품질 확정→ready 증명 연결→대량 그룹 분리→train-only tokenizer 순서로 진행한다. 기존 완료 도구/소량 검증과 실제 데이터 통과를 구분한다.
- 데이터 대기 중 REF-DATA의 sampler 경계(D01,D02), 코드 의미 보존(D03), REF-TOK의 범위·캐시(T01,T02)를 현재 코드 테스트에 연결한다.
- 평가 준비를 리허설 이전으로 앞당긴다. E01~E03 및 실제 한/영 holdout·고정 생성 평가와 REF-TRAIN의 분산·재개 검증이 끝나야 1B 리허설을 시작한다.
- REF-EXPORT는 HF 변환 평가를 처음 사용할 때 선행한다. 자체 모델 평가·데이터 준비를 export 때문에 일괄 보류하지 않는다.
- SFT는 S01/S02/T02/E02, 선호학습은 도입 결정 시 P01을 적용한다. 과거 checkpoint 복구·과거 점수 재현은 별도 비교 작업이다.
- 원안 일정의 마지막 평가일은 최종 종합 평가를 뜻한다. 기본 평가 구현과 검증을 마지막 날로 미루지 않는다. 실제 처리량과 추가 검증 시간을 반영해 일정을 재산정한다.

현재 실제 후속 실행 순서·파일 범위·완료 기준·선행 LLM REF 재대조는 WORKFLOW 최상단 및 FRANKENSTALLM_HANDOFF의 2026-11-06 항목을 따른다. 원안 일정은 현재 확정 일정이 아니며 감사/대량 split/실제 모델 파일럿 후 다시 산정한다.
