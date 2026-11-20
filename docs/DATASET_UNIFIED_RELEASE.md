# 메인 저장소 전체 데이터 통합

## 최종 완료 (2026-11-07 UTC)

메인 [korean-pretrain-corpus v1.1.0](https://huggingface.co/datasets/pathcosmos/korean-pretrain-corpus/tree/v1.1.0)에 실제 **3,728파일·502,632,292,739bytes**를 공개했다. 최종 commit은 `b9fd2dae2d00acfea0b1068dd25aab4195a42a1d`이며 현재main과태그의일치를확인했다. 6소스192문서 실제읽기·CPU toy save/resume·전체원격파일/체크섬 검증 통과. 전체3B 독립 재현·알려진 출처/고지 공백은 별도 범위로 유지한다.

기존 본문과 한국어→영어/13언어구성을 보존하고, **개별6저장소 카드와 메인14카드 총20개에 개별 저장소/종합 내부 폴더 링크를 나란히 표시**했다. 공식 hf.co 링크는 기존 개별 저장소로 연결된다. 본문보존·12링크익명접근·실제메인14카드의두링크/CHECKSUMS 검증 완료. `runs/public_card_dual_links_v1/report.json`, `navigation.done` 및 `runs/dataset_unified_v1/report.json`, `release.done`을 최종 증거로 사용한다. 아래 진행/중단 기록은 역사적 실행 기록이다.

사용자 최신 지시에 따라 실제 전체 파일을 `pathcosmos/korean-pretrain-corpus` 한 곳에 넣는다. 소스별 폴더는 같은 저장소 내부 디렉터리다. 과거 소스 저장소 링크만 제공하는 구조를 최종 사용자 경로로 사용하지 않는다.

## 최신 재개 (2026-11-07 22:35 UTC)

사용자 승인 ‘그렇게 해’에 따라 **이번 업로드 프로세스의 RSS 보호 한도를16GiB로 조정해 재개**했다. 기본값4GiB는 유지하고 `--run --rss-limit-gib 16`을 명시한다. 본학습·CPU/worker·업로드 namespace·고정 데이터 계약은 변경하지 않는다. 집중9tests 및 기본4/명시적16/잘못된CLI값거부 검증 통과.

완료2,392파일·306,061,061,664bytes receipt와 이전 오류를 보존하고 재개 전 전체3,679입력stat/고정spec SHA·receipt중복/bytes·write계정·원격head·프로세스종료/lock을 대조했다. native PID564464(독립세션/부모1)에서 실제 보호16GiB 상태를 확인했다. 이후 **새96파일·6,793,350,824bytes 추가 검증**, 합계2,488파일·312,854,412,488bytes로 진척을 확인했다. 전체 통합과v1.1.0 발행은 아직 미완료다.

근거: `rss16_recovery_admission.json`, `rss16_resource_admission.json`, `rss16_recovery_started.json`, `rss16_recovery_status.json`, 최신 `upload_state.json`/`release_state.json`. 위 파일은 `runs/dataset_unified_v1/` 아래에 있다. 파일 캐시를 포함한 프로파일의 보수적 headroom은0으로 표시돼, inactive_file/비캐시 working set과 별도예산을 추가 대조했다.16GiB publisher+기존main/final 미래예산928GiB+예비128GiB=1,072GiB<1,920GiB, NFS/로컬200GiB 여유·캐시출력2GiB 보존을 확인했으며 캐시 강제제거나 시스템메모리한도 변경은 하지 않았다.

- 새 버전: `v1.1.0`. 기존 `v1.0.0` 태그와 원본·기존 저장소는 보존한다.
- 경로: `wiki_ko/`, `fineweb2_ko/`, `hplt_ko/`, `fineweb_edu/`, `owm/`, `github_code_v1/` 안에 실제 text/tokens/splits/offsets/출처/고지를 배치한다.
- 전송 계약: `runs/dataset_unified_v1/upload_spec.json`, **3,679파일·502,615,554,099bytes**. 루트 최종 문서·코드·tokenizer 등은 별도 소규모 최종 commit으로 갱신한다.
- 소스 manifest 경로만 메인 내부 경로로 변경한다. 본문·토큰 bytes, 원래 파일명/행번호·inventory ID·본문 SHA·canonical split·upstream URL/revision은 보존한다.
- 전체 다운로드 예제는 메인 저장소 snapshot_download 한 번으로 전체 파일을 받는다. 텍스트 all 뷰에 validation이 포함되므로 train 필터를 명시하도록 유지한다.
- 기존 포터블 mix/main recipe가 이미 `<source>/tokens/...`를 사용하므로 이 통합 배치와 일치한다. source_repositories는 모두 메인으로, source_paths는 폴더로 연결한다.

## 실행과 보호

`scripts/consolidate_public_corpus.py --run`은 고정 spec과 atomic receipt로 기존 검증 uploader를 실행하고, 모든 데이터 성공 후 6소스 익명 소비·CPU toy 학습/평가/save-resume 검증·최종 카드/체크섬/버전 발행까지 진행한다.

최초 카드 링크 추가 동시 commit을 별도 보존한 뒤 최신 카드로 합쳤다. staging 카드의 license_link HTTPS 조건을 사전 검증했다. 실제 최초 metadata commit 후 링크나 metadata만으로 전체 완료를 표시하지 않는다.

기존 LFS 경로는 다른 저장소의 객체에 대해 새 전송을 요구하는 것을 확인했다. Xet 경로의 실제 표본 126,760,479bytes/5.818초 및 978,430,276bytes/11.985초·peak750.75MiB를 측정했다. 최초 병렬 Xet 시도는 RSS4GiB 보호에 걸려 중지됐으며 OOM은 아니었다. 완료 receipt와 입력을 보존하고 **동일한 RSS4GiB 보호를 유지한 채** hf_xet1.1.9의 파일 ingestion1/upload2로 낮췄다. 구버전 실제 환경변수명을 설치 binary에서 확인했으며, 새 버전 호환 키도 함께 설정했다.

- CPU88..95/hash4, Xet ingestion1/upload2, GPU0, chunk cache0, 로컬 shard cache상한1GiB(`/tmp/korean-pretrain-corpus-xet`), 대형 로컬 복사0.
- 96파일/32GiB batch, atomic receipt, 원격 LFS file SHA256/size·작은 Git 본문 SHA, 불일치·quota 오류 중지, 유료 전환/자동 삭제 없음.
- 실제 detached PID는 `runs/dataset_unified_v1/release.pid`. `release_state.json`을30초 갱신하고 기존 watcher registry `corpus_unified_v1`에 연결한다. 모델 주기 호출이나 외부 메시지가 아니다.
- 로그/제어: `upload_state.json`, `receipts/`, `release.log`, `release_state.json`, `error.json`, `payload_report.json`, `consumer_progress.json`, `consumer_validation.json`, `final_commit.json`, `report.json`, `release.done`.

## 검증과 완료 조건

집중9tests 통과: canonical identity/upstream 보존, source 경로 재배치, 6개 단일 저장소 YAML config, 전체 다운로드 예제,13언어 유지, root/source 체크섬 포함, 실패 후 재실행의 태그·commit 재사용. offline fake API 조립 검사는 실제 전체 전송 완료와 구분한다.

**실제 메인 첫 묶음 검증:** commit `4a9cb2130c418f4baf6225f990dc5450367fa63d`,130파일·32,167,096,066bytes가 익명으로 존재함을 확인했다. 메인 폴더의 위키 train16/validation16문서 streaming 및 실제 메인 token으로 CPU toy 학습/평가·저장재개 두 번째 update exact 통과. `first_main_consumer_validation.json`.

최종 완료는 모든 spec 파일의 메인 공개 검증, 6소스192문서 소비 및 CPU 재개 검증, 모든 실제 파일을 포함한 CHECKSUMS와 파일 목록의 일치,13언어 카드·6개config·release.json 및 메인 v1.1.0 태그가 발행된 뒤 `report.json`/`release.done`으로 판단한다. 현재는 통합 전송 중이며 완료가 아니다. 원문 URL/Git commit/독립 고지의 기존 공백과 전체3B 독립 재현은 별도 미완료 범위로 유지한다.
