# GH·HF 전체 언어 README와 교차 링크 적용 계획

2026-11-08. 사용자 진행 지시에 따라 R1~R5 구현·검증·원격 반영 완료.

## 실행 결과

- GH: https://github.com/pathcosmos/korean-dense-llm/releases/tag/v0.1.1 . 최종 commit `aef52f10fdf19da64413dab0c89add4deec727c1`.
- HF 통합 corpus 문서 tag `v1.1.1`, 실제 학습 입력 `v1.0.2`, 소스별 wikipedia/fineweb2/hplt2/fineweb-edu/math/code 문서 tag 각 `v1.0.1`. 데이터 고정 버전과 기존 tag는 그대로 보존했다.
- GH15+HF112=127README, 13언어, 기본 README 한국어 전체→영어 전체 및 언어별 상호/GH↔HF 연결 완료. GH영어기존주소 호환 유지. GH15개/HF128개 문서·메타데이터 익명 bytes/SHA검증 통과.
- 183종 문서 링크의 대상, 언어별 필수 절, Python예제구문, HF YAML원형, 체크섬정합 검증 통과. HF전체tree에서 문서변경밖파일의 blob/LFS/크기 동일과 기존tag 보존 확인. 기존 운영코드/설정241개 SHA불변 확인.
- 새 reader예제는 고정v1.1.0의 실제wiki train1행 읽기8.67초 통과. 초기 HFcache 심볼릭링크의 sibling import 실패는 일반 local_dir helper다운로드로 해결·재검증했고 payload/reader구현은 바꾸지 않았다.
- 최종GH CI https://github.com/pathcosmos/korean-dense-llm/actions/runs/42453820906 :258passed/35skip/0fail,31.92초. skip은 원래운영기록/별도파일시스템조건. 문서검증 결과와 기존 모델검증 범위를 구분한다.
- 근거 `runs/readme_crosslinks_v1/{snapshot,local_validation,hf_publication,public_verification,production_invariance,ci_final,final_report}.json`, 저장소별receipt, reader검증/로그, before/after. 공개 검증기록은 GH v0.1.1의 `multilingual-verification.json` 첨부물이다.
- 요청한 문서공개에 남은 필수작업 없음. 아래는 실행 전 확정한 설계와 완료 조건의 기록이다.

## 목표

GitHub `pathcosmos/korean-dense-llm`과 관련 Hugging Face 데이터 저장소의 README를 한국어 전체 → 영어 전체 순서로 구성한다. 기존 HF 13개 언어에 맞춘 완전한 언어별 README를 제공하고, 언어 간·저장소 간·GH↔HF 이동을 양방향으로 연결한다.

원격 현황 근거: `runs/readme_crosslinks_plan_v1/remote_inventory.json`. GH README는 연결 앱으로 직접 확인했다. HF는 인증 없는 metadata/README 조회만 수행했고 데이터 payload는 다운로드하지 않았다.

## 실제 확인한 대상과 문서 수

| 대상 | 현재 | 적용안 |
|---|---|---|
| GH korean-dense-llm | 한국어 README.md + 별도 README.en.md | README.md에 한국어 전체/영어 전체, cards/에13언어, 기존 README.en.md 유지 |
| HF korean-pretrain-corpus | 한영 README +13언어 카드 | 한영 완전성 점검,13언어 내용·상호/GH링크 갱신 |
| HF korean-pretrain-training-data | 한영 README +13언어 카드, 일부 번역은 짧은 소개 |13언어를 사용법·검증·한계까지 완전하게 확장 |
| HF korean-pretrain-{wikipedia,fineweb2,hplt2,fineweb-edu,math,code} | 각 한영 README만 존재 | 각13언어 카드 신규 작성 및 기본 README·탐색 링크 갱신 |

대상은 GH1개·HF8개다. 계획상 README는 GH15개(기본1+언어별13+기존영어1)와 HF112개(8×14), 합계127개다. 체크섬·언어 manifest·링크 검사/보고 파일은 이 수에 포함하지 않는다. 과거 모델 HF 저장소 등 관련 없는 저장소로 범위를 확장하지 않는다.

언어 코드는 기존 표기를 유지한다: `ko`, `en`, `zh-CN`, `zh-TW`, `ja`, `fr`, `de`, `es`, `pt-BR`, `hi`, `ar`, `he`, `ru`.

## README 형식

1. 루트 README.md: 프로젝트/자료 제목과 언어 탐색 → 한국어 전체 본문 → 영어 전체 본문. 한영 설명을 항목마다 번갈아 배치하지 않는다. HF YAML front matter는 파일 맨 앞에1회 유지한다.
2. cards/README.<lang>.md: 해당 언어의 완전한 본문, 전체13언어 탐색, 기본README 복귀, 같은 언어의 관련 GH/HF 문서 링크. 언어코드·경로를 GH와HF에서 통일한다.
3. 기존 GH README.en.md: 기존 링크 호환을 위해 영어 전체 문서를 유지한다. cards/README.en.md와 같은 원본에서 생성해 내용 차이를 방지한다.
4. 각 저장소의 한국어/영어 카드도 해당 언어 전체 문서다. 루트 한영본문과 동일한 원본으로 생성한다.
5. 일반 설명·주의·검증·한계 문단을 번역한다. 코드/CLI/경로/소스명/해시/버전은 의미를 바꾸지 않는다. 짧은 소개문에 영어 공통문단만 덧붙인 상태를 전체 번역 완료로 간주하지 않는다.

공통 내용은 프로젝트 목적·구성·사용법·검증·벤치/최적화·출처/라이선스·한계·관련 저장소다. 역할에 따라 다음 내용을 보강한다.

- GH: 설치, 합성CPU실행, 소스구조, 실제학습recipe, 최적화·벤치근거, 공개본검증.
- HF 통합corpus: 전체구성·규모, 소스별폴더, train/eval·tokens·split·tokenizer, 실제reader, 코드/학습입력/개별자료 안내.
- HF 실제학습입력: 실제726train+6eval=732bin, config평가우선순위, 토크나이저로드, 원mix·원config의관계, 전체corpus와차이.
- HF 소스별6개: 해당소스의출처·정제/중복정책·분할/파일/컬럼·소스별권리/고지·통합본내같은소스폴더·사용예제. 다른저장소수치를복사하지않는다.

README의 번역 언어와 학습 데이터의 언어는 별개라는 설명을 모든 언어에서 유지한다. 코드 MIT·원본문서 CC BY4.0과 원본자료별 조건도 구분해 유지한다.

## 링크 연결 규칙

| 출발 | 목적지 |
|---|---|
| GH 기본README | HF 통합corpus, 실제학습입력, 소스별6개; 프로젝트설명·벤치·검증문서 |
| GH 언어카드 | 같은언어의HF통합/실제입력/소스별카드, 다른12언어, GH기본README |
| HF 기본README | GH프로젝트·사용법·벤치·검증, 관련HF저장소와해당소스폴더 |
| HF 언어카드 | 같은언어의GH카드와관련HF카드, 다른12언어, 해당저장소기본README |
| 개별소스HF | 통합HF의해당소스폴더와동일언어설명, GH파이프라인 |

일반탐색은 현재main의문서를가리키고, 재현용코드/데이터예제는고정tag/commit을명시한다. HF카드는HF의blob/tree형식으로, GH카드는GH형식으로경로를생성한다. README.en.md·cards/간상대경로와앵커를각플랫폼기준으로검사한다. 존재하지않는번역경로로조용히연결하지않는다.

## 실행 단계와 완료 조건

| 단계 | 작업·산출물 | 통과 기준 | 권장 개발 모델/effort |
|---|---|---|---|
| R0 현황·계획 | 9개 저장소·13언어·기존 경로 확인 | 이번 읽기 전용 조사 완료; 정확한 변경 목록은 R1에서 고정 | GPT-6.1 Sol / Medium |
| R1 내용·링크 계약 | 원격 head·문서 before SHA·기존 YAML/체크섬·링크 목록, 저장소별 사실표 | 대상 경로·수치·버전·본문 필수 항목 고정 | GPT-6.1 Sol / High |
| R2 원문·전체 번역 | 한영 완전 본문, 13언어 카드, 언어별 핵심 용어와 파일별 diff | 사용법·검증·출처·한계 포함; 같은 사실·명령어·버전 반영 | GPT-6.1 Sol / High |
| R3 로컬 조립·검증 | 공통 생성 원본·언어/링크 manifest·검사 보고서·공개 diff | 내용 동등성·필수 항목·127개 README 경로·링크·YAML·체크섬 검사 통과 | GPT-6.1 Sol / High |
| R4 문서 발행 | GH 문서 patch·HF 저장소별 문서 commit·단계별 receipt | 원격 head 대조, README/card와 필요한 metadata 갱신, 기존 버전 보존 | GPT-6.1 Sol / Medium |
| R5 공개 확인 | 익명 조회·링크 순회·표시 확인·최종 변경 목록 | 최종 공개 commit에서 언어별 문서·양방향 링크·체크섬 확인 | GPT-6.1 Sol / Medium |

## 작성·검증 방법

- 언어코드·표시명·저장소 ID·소스 폴더·버전·기준 수치를 하나의 manifest로 관리한다. 문서는 공통 구조와 저장소별 내용에서 생성하고 숫자·링크를 언어마다 손으로 바꾸지 않는다.
- 기존 문서 생성 스크립트의 재사용 가능 부분을 확인한다. 기존 publisher 재실행에는 데이터 공개 작업이 포함될 수 있으므로 새 문서 전용 실행 경로를 사용한다.
- 모든 문서에 언어 탐색·필수 절·동일 언어 교차 링크가 있는지 검사한다. 명령어·숫자·단위·ID·라이선스와 미실행 항목의 상태가 일치하는지 확인한다. 본문 의미와 번역 누락은 별도로 검토하며 링크 검사만으로 번역 품질 통과를 선언하지 않는다.
- GH/HF 상대 경로, README 내부 앵커, 같은 언어 문서 링크, 기존 영어 URL 호환을 확인한다. 발행 전에는 로컬 후보 파일로 경로를 해석하고 발행 후에는 익명 HTTP 조회로 실재·내용을 확인한다. 중복 URL은 한 번만 조회해 통신을 줄인다.
- HF YAML의 language/license/tags/configs/data_files를 보존하고 카드 구문·reader 참조 경로를 확인한다. 설명문 번역 언어를 dataset의 language 필드에 추가하지 않는다.
- GH release/files.json과 필요한 public_changes/validation 기록을 갱신한다. HF CHECKSUMS 등은 실제 schema에 맞춰 변경 문서 기록을 갱신하고 불변 payload hash를 재사용한다. README를 바꾼 뒤 이전 체크섬을 그대로 남기지 않는다.
- 문서 수정에 전체 학습·GPU 검사·대형 데이터 재해시를 반복하지 않는다. 예제 명령을 바꾼 경우에만 해당 작은 CPU/reader 검증을 수행한다.

## 발행 순서와 버전 관리

1. GH/HF 전체 후보 문서를 로컬에서 작성하고 diff·문서 수·검증 결과를 정리한다.
2. GH의 13개 언어 문서를 먼저 공개한다. 이때 HF 링크는 이미 존재하는 기본 진입점을 사용한다.
3. HF8개 저장소의 언어 문서를 정비하고 이미 공개한 GH 동일 언어 문서로 연결한다. 저장소 안에서는 README·cards·필요 체크섬을 같은 commit에 묶는다. 여러 저장소를 원자적으로 동시에 갱신할 수 없으므로 receipt로 부분 진행을 기록한다.
4. HF 동일 언어 페이지의 실재를 확인한 뒤 GH에서 최종 동일 언어 링크를 반영한다. 전체 링크 최종 검증 뒤 공개 완료로 보고한다. 아직 없는 목적지를 가리키는 중간 상태를 완료로 표시하지 않는다.
5. GH는 문서 patch판 v0.1.1을 후보로 한다. HF는 R1에서 현재 tag·commit 규칙을 확인한 뒤 필요한 문서 patch판을 정한다. 기존 GH v0.1.0과 HF tag는 이동·덮어쓰기하지 않는다. 데이터 고정 버전과 문서 새 버전을 구분해 설명한다.
6. 각 commit에 parent head를 고정하고, 다른 갱신이 있으면 최신 diff를 다시 읽는다. 일괄 문자열 치환으로 다른 변경을 덮어쓰지 않는다.

## 범위와 최종 보고

공개 README·언어 card·필요한 문서 생성/검사 코드·체크섬/문서 증명이 대상이다. HF 데이터 payload·tokenizer·mix·학습 config·학습 코드·비공개 저장소·진행 중 학습은 변경 대상에 포함하지 않는다. 과거 산출물과 tag는 보존한다.

사용자의 진행 지시에 따라 public 방침을 재확인하지 않고 이 범위의 로컬 작성→검증→공개를 완료했다. 판단이 필요한 예외는 기존 사용자 지침에 따라 근거·영향·선택지를 보고한다. 미래 자동 문서 갱신을 예약하지는 않았다.

최종 보고에는 GH/HF 대상 목록·언어별 URL·작성/수정 수·고정 commit/새 tag·링크/내용/YAML/정합 검증 결과·payload 불변 근거·미완료 범위가 있다면 해당 항목을 포함한다. 서브에이전트는 사용하지 않는다.
