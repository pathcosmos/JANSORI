# 전체 public 업로드 실행 기록과 운영 경로

**최신 배포는 메인 단일 저장소 v1.1.0으로 통합 완료:** [DATASET_UNIFIED_RELEASE](DATASET_UNIFIED_RELEASE.md). 실제3,728파일·502.63GB가메인에있고, 기존개별저장소링크와메인내부링크를20개카드에병기했다. 아래6저장소/v1.0.0기록은기존공개이력으로보존한다.

사용자 최신 지시: **전부 업로드**, 무료 용량 사전 문의 없이 시도, 용량 한도 오류 시 중지, 결제 없음.
기존 6개 소스 스냅샷의 public 배포와 최종 카드·버전 발행을 완료했다. 문서별 출처·고지의 알려진 공백과 독립 환경의 전체 3B 학습 재현은 별도 미완료 범위로 유지한다.

## 최종 발행 완료 (2026-11-07 20:43 UTC)

- 안내 저장소: [korean-pretrain-corpus v1.0.0](https://huggingface.co/datasets/pathcosmos/korean-pretrain-corpus/tree/v1.0.0), commit `902118f4cffd04f12542c1485aaafb2c6ee8f5cf`.
- 6개 소스 전송 계약 **3,672파일·502,615,498,156bytes** 전부 원격 크기/SHA 대조 완료. 중단된 감시 기록을 보존하고 기존 receipt로 재검증했으며 추가 payload 전송 없이 감독기 성공 증명을 복원했다.
- 6개 소스와 안내 저장소 모두 `v1.0.0` 고정 태그 발행. root `release.json`에서 소스별 최종 commit을 고정했다. 한국어→영어 본문 및 13개 언어 문서 상태 갱신, HF YAML 검색 태그 설정. 실제 데이터 언어와 문서 번역 언어는 구분한다.
- 실제 익명 소비 검증: 소스마다 train16/validation16, 합계192문서의 canonical split 읽기·표본 교차 없음 확인. 공개 token range로 CPU toy LM의 finite 학습/평가와 저장·재개 두 번째 update exact 확인. 전체 3B 학습 재현 증명은 아니다.
- 최종 익명 검증: root CHECKSUMS의47개 항목 전체 대조·실제 파일 목록과 일치·캐시 제외, 13개 번역 문서, root/6소스 태그와 고정 commit·카드 YAML·manifest SHA 대조 통과. 출처별 원본 라이선스 범위와 기존 미확인 항목 유지.
- 적용 파일: `scripts/finalize_public_corpus_v2.py`의 metadata 생성, `tests/test_corpus_finalizer.py`의 라이선스/분리 config 보존 검사. 집중8tests 통과. 최종 실제 근거: `runs/dataset_upload_v1/{supervisor_report,finalizer_report,final_public_verification}.json` 및 `final_metadata/consumer_validation.json`.
- 다음: 사용자 배포 작업 완료. 향후 별도 작업은 나머지 원문 URL·Git commit·독립 고지 보완과 전체 독립 학습 재현이다. 정상 조회 Luna/Low, 새 수정 Sol/Medium 권장. 원본·기존 본학습 producer와 설정 변경, 유료 서비스 사용 없음.

## 실행 대상과 표현

- 안내: `pathcosmos/korean-pretrain-corpus`.
- 소스: `korean-pretrain-{wikipedia,fineweb2,hplt2,fineweb-edu,math,code}` 모두 public.
- 소스 전송 계약: `runs/dataset_upload_v1/upload_spec.json`, **3,672파일/502,615,498,156bytes**.
- 포함: 기존 정제 텍스트726, train/eval bin1452, canonical split726, token offset726, 위키 provenance/생존행 해소 및 source별 카드/고지/manifest/로더.
- 안내 저장소의 tokenizer·코드·환경·recipe·13언어 문서·상세 감사/재작업 metadata는 별도 공개한다.
- 원본 정제 Parquet bytes/행번호를 보존해 `text/`에 올린다. `original_documents/all`에는 train과 eval이 모두 있다. **학습에는 제공한 canonical split reader로 train을 명시**해야 한다. token bin은 이미 train/validation으로 분리돼 있다.
- 이 선택으로 정제 텍스트 약248GB를 새로 복제/재직렬화하지 않으며 기존 내용 SHA와 행 ID를 유지한다. 기존 공개 계획의 물리적 `data/train/*.parquet` 재패킹안보다 실제 `text/ + splits/ + reader` 계약을 우선한다.
- 위키 문서별 URL 후보는 제공한다. 나머지 웹 URL 복원·코드 원 Git commit/독립 LICENSE/NOTICE 확인 범위는 source.json/카드에 미확인 상태로 기록한다. 기존 스냅샷 전체 게시를 권리 전수 확정으로 표시하지 않는다.

## 검증과 재개

`scripts/upload_public_corpus.py`는 고정 allowlist의 bytes/SHA256/mtime/inode를 검사한 뒤 공개 SDK로 전송한다.
대형 파일의 원격 LFS file SHA256/크기를 확인하고 작은 Git 파일은 익명 GET 본문 SHA256도 확인한다.
전체 약503GB를 다시 다운로드하는 대신 내용 주소 증명을 재사용하며, 실제 익명 소비 표본은 별도로 검사한다.

24파일/약4GiB 단위 commit, hash4/HTTP-LFS4/CPU88..95, GPU0, RSS예산4GiB이다.
단일 파일이4GiB보다 크면 한 파일 batch를 허용한다. Xet local cache·hf_transfer를 켜지 않고 추가 대형 로컬 복사0으로 실행한다.
실제 완료 파일/블롭은 서버 내용 주소와 로컬 receipt로 재사용한다. 아직 완료되지 않은 단일 multipart 파일의 바이트 단위 재개까지 보장하는 방식은 아니다.

- `upload_state.json`: 현재 phase/commit 확인 파일 수/bytes/활성 저장소.
- `receipts/*.json`: atomic batch 증명, 재개 시의 기준. `upload_receipts.jsonl`은 추가 이력이다.
- `upload.log`: 전송 로그. 상태 조회 시 임의 HTTP 로그를 그대로 출력해 인증/서명 URL을 노출하지 않는다.
- `supervisor_state.json`: 30초 관측 및 RSS 보호 상태.
- `supervisor_report.json`, `supervisor.done`: 전체 소스 전송 결과와 내용 증명. 성공 여부를 먼저 확인한다.

초기6개 metadata commit+위키본문 전송을 실제 별도 프로세스로 종료한 뒤 재실행했고, 완료된57파일/약1.24GB를 다시 보내지 않고 이어가는 것을 확인했다.
storage quota 오류는 `stopped_quota`로 고정하고 자동 재시도·유료 전환을 하지 않는다. 영구 권한/무결성 오류도 멈춘다.
실행 중에는 같은 supervisor를 중복 시작하지 않는다. 재개/변경 전 PID·증명·오류 원인을 먼저 확인한다.

## 실제 서버 작업

- 전송 감독기 `scripts/supervise_corpus_upload.py`, PID 파일 `supervisor.pid`.
- 전송기 PID 파일 `uploader.pid`; 감독기가 해당 프로세스만 RSS 한도 보호한다.
- 서버 감시 registry: `corpus_public_upload_v1`, 기존 watcher30초에 연결.
- 마무리 `scripts/finalize_public_corpus_v2.py --wait`, PID 파일 `finalizer.pid`.
- 마무리 감시 registry: `corpus_public_finalize_v2`, 기존 watcher30초에 연결.

소스 전송 성공 후 마무리 프로그램이 6개 source commit/tag를 고정하고, 익명 train/validation 읽기와 실제 public 토큰을 사용하는 작은 CPU 학습/평가/저장·재개를 검사한다.
성공한 경우에만 전체 release.json, 카드의13언어 상태, source/index `v1.0.0`을 발행한다.
실패/용량 오류라면 전체 완료로 표시하지 않는다. `finalizer_state.json`, `finalizer_report.json`, `finalizer.done`과 서버 이벤트에 기록한다.

이들은 실제 로컬 프로세스다. 모니터링은 모델을30초마다 호출하지 않으며 외부 푸시/메일이나 대화 종료 후 자동 메시지를 전송하지 않는다.
실행 중인3B 학습 producer/설정·원본과 final token 파일은 변경하지 않는다.

## 지금까지 실제 검사

- 전송/분리/offset/reader 및 마무리 조립·재실행18개 검사 통과. 마무리 조립 테스트는 fake API를 사용한 offline 검증으로 실제6소스 전송 완료 증거와 구분한다.
- 위키 공개 데이터 익명 streaming train16/eval16문서와 공개 train/eval token range로 작은 CPU LM 학습·평가·save/resume exact 검증 통과. 실제3B 전체 학습 재현을 대신하지 않는다.
- 공개 SDK snapshot_download 및 AutoTokenizer에서 원본 tokenizer bytes SHA, 전체어휘65536와 special16 IDs 일치 확인.
- 코드/문서/토크나이저 등 안내 저장소의 전체 CHECKSUMS 항목은 익명 접근으로 확인했다. 생성 Python cache는 공개 목록에서 제외했다.
- Git JSON에 대한 filesystem 전체 읽기 오류는 identity HTTP JSON 요청으로 해결했고 실제 공개 데이터 소비 검증으로 확인했다.
- mtime granularity가 같은 임시 fixture에서도 SHA 검사로 변조를 거부함을 확인했다. fixture stat 분기 검사는 timestamp를 명시해 안정화했다.

작업 상세 증거는 `runs/dataset_upload_v1/`의 preflight/admission/consumer/SDK/registry/report JSON을 따른다.

## 카드 상호 링크 후속 반영 (2026-11-07)

`runs/public_card_links_v1/report.json`: 모든 카드20개에 통합 안내와 개별6개 저장소 링크 반영. 루트 CHECKSUMS 포함21파일 익명 SHA 검증 통과. 현재 main 문서만 갱신했으며 v1.0.0 고정commit과 기존7개 저장소/데이터는 보존했다. 기존 최종검증 보고서는 원래 릴리스 시점 증명으로 유지한다. 새 main 문서의 증명은 별도 receipt를 사용한다.
