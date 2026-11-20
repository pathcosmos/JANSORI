## 로컬 reader 검증·성능 적용 / F4 분포 선행 준비 (2026-11-06 16:51 UTC)

이전Goal턴은progress(공유검증/읽기snapshot구현·8검사·NFS병목증거)로분류했다. 이번턴도실제코드·검사·실측과전체데이터읽기단계기동으로진행했다. 전체Goal F4~F9범위유지,아직미완료.

- REF-DATA/TOK: `data/local_split_reader.py` + `VerifiedSplitContext(reader_manifest=...)`로원본영구manifest를논리입력으로유지하면서실제DB는같은SHA의닫힌로컬파일에서읽는다. 원래contract/counts/policy와경로외모든내용동일성을대조하고fresh로컬DB SHA/무결성/누락/교차검증을수행한다. 원본/reader manifest변경도감지. 복사필요시prefix바이트를대조해부분복사재개·전체SHA/fsync/rename,잘못된partial은보존·거부한다. 기존builder의보존로컬DB를재사용하면추가115GB복사는불필요.
- 검사: local-reader3개/0.790초·공유입력2개/0.404초·독립quality입력2개/0.501초·F5gate/byte-budget/효율판정3개/0.015초통과(총10). canonical표본식별·내용동일,같은크기변조/정책/marker거부,복사재개,전체4범주·그룹단위baseline제외·독립eval원문,범주누락/partial/변조거부,기존checker실행을확인. 단위성공과실제최종tokenizer통과는구분.
- 같은3M행fixture의로컬전체비교 `runs/split_distribution_native3m_local_v1` 완료증명검증: 복사50.589초+fresh검증21.532초,기존source별분포15.112초 vsnativeTEMP1thread7.463/8thread7.310초,전체102.643초·peakRSS약2.96GiB. native집계약2.07배,counts/source별그룹·최대component동일. 앞선NFS240초partial은보존. warm-cache/저장소차가있어전체corpus보장속도는아님.
- 적용: `scripts/run_final_tokenizer.py`로 **runs/f5_tokenizer_v1**를기동·30초registry등록했다. 현재 **verify_local_context** 단계이며child PID235248생존확인. 지금허용된것은F4후속의읽기전용fresh로컬검증/실제분포집계다. **train표본추출/BPE는F4 controller의최종완료증명과분포report SHA에연결된review(decision=proceed) 둘다있어야진행**한다. review는Goal작업에서실제분포근거를검토해작성하며사용자승인을매번요구하는장치가아니다.
- 준비표본/학습/검사전체는동일livecontext·read snapshot재사용. 승인된6source 3,000,000,000byte정책·raw65536·Rayon16streaming. 독립검사eval문서는source당1024개/전체원문,기존baseline이학습한그룹전체를제외한다. baseline원본행ID는영구아카이브해local scratch유실에대비. 효율기준은학습전명시:비교baseline대비각범주token수비율≤1.10·정책가중tokens/byte비율≤1.02. 이는독립source-balanced검사에대한공학적guardrail이며모델능력/전체corpus품질보장이아님. 초과하면needs_review로남겨조사하고통과로꾸미지않는다.
- 자원:CPU0..63/OMP2/NumExpr2/Rayon16/hash32/로컬TMP,기동전headroom615GiB. reader cache는실제DB115.78GB×1.2인약129.4GiB(소형fixture상한승계없음),메인F4완료전F5 RSS256GiB/완료후384GiB,local192GiB/durable96GiB/reserve각200GiB/24h감독. producer코드14파일snapshot·contract SHA고정,실행중해당파일변경금지.
- F4 본작업은계속verify_published이며현재최종완료증명은아직없다. 생산7개파일SHA불변/새오류·OOM0. 다음은실제분포검토→F4최종완료뒤3GB표본·BPE·독립검사→실제전체파일tokenpilot. 권장Sol / High유지,실제8GPU검증Astra / High. 본학습미시작.

## 최종 저장·검증까지 자율 진행 유지 (2026-11-06 15:41 UTC, 서버시각 기준)

사용자가F4최종저장·검증까지추가확인없이계속진행하고예외를알리도록지시했다. 기존producer는finalize→전체무결성/교차/count검증→닫힌DB복사SHA→영구원자발행→독립consumer전체coverage/무결성/교차검증→완료증명을순서대로자동수행함을코드재확인했다. 현재finalize진행중이며원producer7개파일은수정하지않았다.

- `scripts/watch_split_finalization.py`, `runs/f4_completion_watch_v1`를새30초로컬관측기로기동하고기존watcher registry에등록. CPU94/95 affinity·OMP/NumExpr/Rayon1·local TMP 명시설정,기동직전hardware_profile실측기록. 매poll모델호출없음.
- 작은progress행과CPU/IO활동을읽어긴SQL의오래된worker heartbeat를보완한다. 무활동20분은예외event/STATUS기록,상위실행기의error/paused·예기치않은종료·완료증명불일치를예외로기록. 기존2초자원감독의RSS384GiB/local300GiB/durable160GiB/reserve200GiB/72h상한유지. 관측기가producer를중지/재시작하지않으며원본·부분산출물을보존한다.
- 상위controller complete확인뒤root/report/contract·최종manifest/.done/publication·입력/code SHA·최종경로·train/eval/파일별총합증명을대조하고완료요약·상태문서를자동기록한다. 대용량DB SHA·SQL검증은같은실행의실제publisher가수행한증명을연결해불필요한재전수검사를하지않는다. 보고서문자열/마커만으로새구현을합격시키는방식과구분.
- REF-DATA/TOK 운영적용: `tests/test_split_finalization_observer.py` **4개/0.342초통과**. marker누락/manifest변조/producer변경/총행수·경로·파일별count/ownership오류거부,긴SQLphase판정,4MiB메타데이터제한,중복DB해시미호출검사. 구문/diff검사통과. 실제최종F4완료는아직미확인.
- 알림은대화중보고+`logs/pipeline_events.jsonl`·STATUS기록이며외부push/대화종료후자동채팅메시지는설정하지않았다. 최종토크나이저/F5/본학습은이관측기가기동하지않는다. 다음분포검토·토크나이저준비권장Sol / High,실제token모델검증Astra / High.

## 병행 준비·최적화 측정 완료 (2026-11-06 12:36 UTC)

- 메인 F4 정상 ingest **65,589,252/128,856,258문서(50.90%, 입력 단계)**. 원 producer7개 SHA동일·새오류/OOM0·30초감시유지. 입력후그룹배정/검증/영구발행은아직남았다. 메인설정은변경하지않았다.
- `runs/next_stage_optimization_v1` + `runs/bpe_memory_v1` **측정완료**, report/contract 완료SHA와현재producer코드SHA재검증. 합계감독측정시간약605.5초로20분탐색예산안. 새집중tests6개·실제worker강제종료/pending재개·기존순차v3 bytes동일·native build_mix소비·복사/발행검증을완료했다. 구현파일/REF연결은아래기동기록,세부검증 implementation_validation.json.
- **토큰화:** 실제텍스트512MiB(536,881,956 UTF8 bytes)/132,305문서를48Parquet로재구성한4source 실험. 코드repo·split은합성이며최종corpus분리와구분. 13개실행모두 **116,473,784 tokens와동일logical ID/SHA**. 1worker73.886초→24worker×Rayon2/batch1024/Arrow1/NFS직접 최종2회중앙18.099초,동일새실행경로에서4.08배. 원본직렬producer의전체corpus속도개선보장으로확대하지않는다.
- 16worker×Rayon3 반복중앙18.807초 vs24×2 18.670초로약0.73%차이:두후보모두보존,전역최적값으로확정하지않음. batch256/4096·Arrow2는관측상개선없음. 검증부분약10~12초가남아있으며추가worker보다검증공유/집계비용을우선관리한다.
- **저장:** NFS직접2회중앙18.099초 vs로컬생성+파일SHA검증·fsync복사·원자발행20.510초. 이작은warm-cache순차쓰기에서는직접NFS가유리. 큰전체파일에서도같다는주장은하지않으며SQLite의로컬이점과구분.
- **BPE512MiB:** 16threads스트리밍65.461초/RSS4.86GiB,32threads70.554초. 추가동일표본RAM선적재16threads는76.234초(적재2.732초포함)/RSS5.48GiB로더느렸고tokenizer의미ID동일. 현후보는**16threads·스트리밍**,RAM선적재미선택. 설정후보 recommended_settings.json에근거SHA와함께저장,기존전역preset미변경.
- **후속조건:** 완료보고서/공유검증은13만행fixture검증범위. 실제128M행의분포집계query/cache/local-reader예산은F4완료후확정하고무분별한재전수검사를피한다. F5생산샘플의기존SplitInputs연결은별도후속이며이번에바뀌지않음. 부분복사staging은보존·거부하고완성staging만재개됨. 실험tokenizer/토큰은최종학습사용금지.
- 다음: F4완료/분포검토→train-only최종3GB/65536토크나이저 및독립검사→6source실제전체파일pilot에서위후보재확인. 준비/구현권장 **Sol / High**,실제token모델수치/재개검증 **Astra / High**. 본학습/외부업로드/원자료삭제없음.

## 다음 데이터 단계 병행 구현·실측 기동 (2026-11-06 12:24 UTC)

사용자 확인 요청에 따라 `runs/next_stage_optimization_v1` 기동,기존30초watcher등록. 메인F4 full_v2는기존설정/코드SHA그대로유지. 개발권장Sol / High,주세션선택effort확인/자동전환없음.

- **REF-DATA/TOK 구현:** `data/verified_split_context.py`가완료된SplitSelection의SHA/논리/coverage검증을부모에서한번수행하고 source_files의이번호출verified records를재사용한다. `scripts/report_split_distribution.py`는source별train/eval문서·그룹·최대component보고서를생성한다. 실제진행중DB에는큰집계를하지않고완료된실험fixture만사용한다. UTF8/token분포는후속pilot항목이다.
- `data/parallel_tokenize.py`:부모검증후spawn worker에파일별배정,기존v3 layout/manifest/uint16/EOS와동일한바이트생성. 각행raw본문SHA와split행coverage,끝의DB/tokenizer/code불변성검사. 파일별pending→완료마커,재개시출력SHA검증,미완료tmp보존,중복실행lock. 실행새로시작할때필수검증은다시수행. 생산모드는감사입력·train-only split-bound tokenizer증명을요구한다. 기존순차producer파일은변경하지않았다.
- **검증6개통과:** `tests/test_parallel_tokenize.py`4개/4.007초(기존순차와1/2worker·다른batch의바이트동일/기존build_mix소비/손상거부/실제worker강제종료후pending재개/미완료split거부/검증SHA재사용/생산진입거부),`tests/test_token_publication.py`2개/0.125초(로컬복사내용·완료staging재개·원본변경거부·미완료stage보존). 구문검사/diff검사통과. 대규모생산적용검증완료로확대하지않는다.
- **실측기동:** `scripts/benchmark_next_stage.py`,512MiB 실제실험텍스트를48Parquet파일/4source로재구성,코드repo라벨은합성/분리도실험용임을명시. 앞선128MiB실험tokenizer로실제Parquet/split조회/encode/uint16/EOS/fsync/SHA 경로측정. 1worker기준→8×6/16×3/24×2→최선worker의batch256/1024/4096→Arrow1/2→상위2개순서교차재측정. 최선설정의NFS직접/로컬생성후검증복사·원자적rename각2회. 이어동일512MiB BPE16/32 비교. 실험tokenizer/출력은최종학습사용금지.
- 자원:CPUaffinity64개/프로세스합RSS192GiB/총측정20분상한/local·durable각64GiB/파일시스템별여유200GiB.2초감독·worker/parent종료HWM확인·하위프로세스그룹정지·partial보존.기동전freshheadroom576GiB이상조건으로메인미래RSS상한도고려한다.새job의진행/완료는state/report/완료증명으로판단하며아직측정미완료.
- 다음:실측결과/메인처리율검토→동일내용의전체시간으로후보선택,최종F4완료·분포확인뒤train-only3GB/최종tokenizer·source별실제전체파일pilot에서재확인. 기존GPU후보미채택/본학습미시작유지.

## 다음 단계 병행 준비·최적 설정 탐색 계획 (2026-11-06 12:15 UTC)

이번은 코드/자원 조사와 실행계획 작성이다. 새 장시간 실험은 아직 시작하지 않았다. 메인 F4는 54,502,085/128,856,258 입력(42.30%)으로 유지, producer SHA 동일·새오류/OOM 없음. 실측 CPU96/RAM 보수적여유737GiB/H2008장 비어있음. 계획 근거 `runs/next_stage_optimization_plan_v1.json`.

1. **F4 완료 보고서 + 검증 공유 준비 (Sol / High, 최우선).** 완료된 fixture에서 source별 train/eval 문서·그룹·큰 component 분포와 검증증명을 묶는다. 실행 중 전체 DB에는 큰 집계를 하지 않는다. SplitInputs는 source_files 내용검증 뒤 다시 SHA를 읽으며 기존 return_verified_records API를 활용할 여지가 있다. SplitSelection 생성도 전체 DB SHA·논리검증을 수행하므로 worker마다 반복하지 않는 같은 실행 내 검증 공유를 별도 구현/시험한다. 필수검증을 없애거나 과거 완료마커만으로 대체하지 않는다. 고정된 현재 producer 파일은 수정하지 않고 별도 모듈/fixture에서 준비, 연결은 메인완료 후.
2. **실제 토큰화 경로의 병렬화·성능 측정 (Sol / High).** 현재 tokenize_source는 파일 순차 loop이며 16worker preset만으로 병렬화되지 않는다. 부모의 고정 계약/파일 배정, worker별 독립 임시출력, 결과집계·원자적발행·실패재개를 준비한다. 앞선 JSONL128MiB 실험에 빠진 Parquet decode→split 행조회→encode→uint16/EOS→쓰기/SHA 경로를 별도 fixture/실험 입력으로 계측한다. worker×Rayon은8×6/16×3/24×2, batch256/1024/4096, Arrow1/2부터 후보를 한 축씩 좁혀 최선2개만 순서교차 반복. 로컬 생성+검증된 NFS발행은 복사·검증시간까지 포함해 직접NFS와 비교한다. 동일 행/분리/토큰SHA·coverage 및 강제종료재개가 채택조건. 최종tokenizer+source별전체파일pilot에서 최종확정.
3. **BPE 표본 확대 탐색 (Sol / High).** 기존128MiB의16threads 최선은 출발 후보. 독립512MiB 실험표본에서16/32 먼저 비교하고 필요할 때8 또는64로 확장한다. 큰표본의메모리·전체시간·tokenizer동일성을 확인한다. 메모리로 미리 읽기와 스트리밍도 읽기/검증 비용을 포함해 비교한다. 현재 split 미완료이므로 실험tokenizer는 최종학습에 사용금지. 실제train-only3GB 후보 단계에서는 선택된 설정을 우선사용하고 병목이 달라질 때만 재탐색한다.

탐색예산 초안: 병행CPU64개/RSS합192GiB/local64GiB/파일시스템별여유200GiB, 측정campaign20분상한. 작은/짧은case에서 최선만확대하며 측정비용이 예상절약보다커지면 추가조합을 줄인다. 시간/RSS상한도달은 partial이지통과가아니다. 새기동직전fresh headroom과메인RSS향후성장분을확인하고 한 번에 무거운CPU/I/O실험하나와가벼운보고서준비만 병행한다. 메인처리량을전후구간으로기록,지속저하시 병행부하를축소하되 source변화와경합의인과를혼동하지않는다.

GPU추가compile sweep은 이번8GPU이득이없어우선순위낮음. 추후실제token입력에서 eager기준 batch/누적배치/입력prefetch를총globalbatch동일조건으로 측정(Astra / High),기존batch2/4측정은재활용해중복실험을줄인다. 공식benchmark/본학습은기존진입조건유지. 추천순서 **1→2→3**, 실제전체F5/F6진입은F4완료후.

## GPU 병행 측정 완료 / 메인 진행 조회 (2026-11-06 11:49 UTC)

- 메인 F4 full_v2 정상 ingest **40,155,753/128,856,258문서(31.16%, 입력 단계 비율)**. 이전11:40조회 대비5,085,748건 증가, 구간 약9,368docs/s. 관측job peakRSS30.49GiB. 입력후 그룹배정·검증·영구발행은 미완료. producer SHA 동일/새오류·OOM0/30초watcher 정상.
- GPU `runs/compile_long_v1` **6/6 case 측정완료**, 모든20개 rank보고서가 bounded 수치기준 통과. report/contract 완료증명SHA·현재sourceSHA·각rank보고서와집계내용 일치를 확인. REF-TRAIN 적용파일 validate_compile_long.py, parallel_job_support.py, test_compile_long_metrics.py. 작은1/8GPU smoke와집중검사 통과에 이어 실제3B 512/4096·두seed·FSDP8 측정까지 완료.
- 마지막8GPU seed712: logits/loss차0,gradient상대L2약1.790%,첫update8.203%;seed711 update8.197%. eager반복 자체update7.517~7.526%로 compile만의 차이라고 해석하지 않는다. 유한성 검사 모두통과,첫update10% 등 사전기준은변경하지 않았다.
- 8GPU 처리량 seed711 eager116652→후보116004tok/s(-0.56%),seed712 eager116126→후보116229tok/s(+0.09%). **뚜렷한8GPU 성능이득이 없어 이번후보는 생산성능설정으로 채택하지 않는다.** 단일GPU512는7~8%낮고4096은약2.7%높았다. 짧은2warm+3timed 합성결과이며 실제token/장기수렴/체크포인트재개는미완료. 근거 review.json(.done),원보고서 needs_review/pending은덮어쓰지 않았다.
- CPU BPE/토큰화성능측정도완료. 다음은 메인F4완료→source분포/교차검증 확인→train-only최종65536 tokenizer 및실제파일pilot,권장 **Sol / High**. 실제token모델검증 시 **Astra / High**. 현재최종tokenizer/본학습 미시작. 새생산설정변경이나추가benchmark기동 없이 결과검토·기록만수행.

## 병행 CPU 성능 측정 완료 / GPU 진단 진행 (2026-11-06 11:29 UTC)

- `runs/tokenizer_resources_v1` 측정 완료 및 report/contract SHA 증명 재검증. 실제32,760문서/134,247,441 UTF-8 bytes를 고정해 비교. BPE Rayon8/16/32/64 각각 **28.51/24.61/27.47/37.55초**: 현재 표본에서16이 가장 빠르고64 대비1.53배. peak RSS1.83/1.93/2.06/2.34GiB. 각 설정1회 측정으로 큰3GB 표본의 보장값은 아니다.
- 실제 uint16/EOS token 출력28,967,881개(수량은 report에 기록) 비교에서 worker4/8/16·Rayon12/6/3의 2회 중앙값 **3.758/2.329/2.020초**. 최선16worker는4worker 대비1.86배. 전체case BPE 의미 ID·원문복원 및 encoding 산출물 내용 ID가 일치했다. 시간은 worker 기동·tokenizer 로드·인코딩·로컬쓰기·SHA 포함, 사전 표본 검증 제외. 작은warm-cache/local-output 측정이며 원본 Parquet 전체파일 경로의 최종성능은 후속pilot에서 확인한다.
- 우선 후보는 **BPE16 threads, tokenization16 workers×Rayon3**. 기기 최대스레드를 무조건 기본값으로 확정하지 않는다. 실제train-only3GB와전체파일pilot에서 재확인 후 생산설정 결정. 실험 tokenizer는 최종 학습에 사용하지 않으며 현재 전역 preset도 아직변경하지 않았다. 근거/한계 `runs/tokenizer_resources_v1/review.json(.done)`.
- 동시 메인 처리율은 BPE 구간9271~9851 docs/s, 짧은 encoding 구간7364~9368 docs/s. GPU reference 복사·실제문서 변화가 겹치므로 CPU 단독 영향이나 '영향없음'으로 단정하지 않는다. CPU sweep은 종료했고 GPU `runs/compile_long_v1`은 실제3B/seq512 두seed 수치 비교 중, 전체측정/생산채택 미완료. micro1/8GPU smoke와 집중2tests 통과 근거는 유지한다.
- 다음 권장: 진행중 GPU 결과 검토 **Astra / High**, F4 완료후 분포검토·최종train-only tokenizer 준비 **Sol / High**. F4실제완료·최종tokenizer·본학습은 아직미완료.

## 병행 성능 검증 실행 (2026-11-06 11:27 UTC)

사용자 실행 요청으로 메인 F4 full_v2를 유지하며 두 독립 실험을 기동했다. 개발 권장: GPU 수치 검토 Astra / High, CPU 준비 Sol / High. 주 세션 모델/effort는 확인되지 않았고 자동 전환·서브에이전트는 사용하지 않았다.

- GPU: `runs/compile_long_v1`, `scripts/validate_compile_long.py`. 3B / seeds711·712 / 1GPU seq512→4096→8GPU FSDP seq4096 순서. eager RMSNorm+Attention+중간 cast 보존 후보를 동일 초기 파라미터·입력·production AdamW 그룹에서 eager와 비교한다. logits/loss/gradient/첫 update, optimizer 전 finite 검사와 2warm+3timed step, 장치/host peak를 기록한다. 순수 합성 진단으로 실제 token checkpoint/재개·수렴·생산 채택을 대신하지 않는다.
- CPU: `runs/tokenizer_resources_v1`, `scripts/benchmark_tokenizer_resources.py`. 실제 고정 128MiB 실험 표본(ko50/en35/code7.5/math7.5 byte 목표), BPE 65536 Rayon8/16/32/64, encoding worker4/8/16·각 Rayon12/6/3·순서 역전 2회. 원본/표본 SHA, BPE 의미 ID, 실제 uint16+EOS 출력 SHA가 동일해야 성능을 비교한다. split 미완료 표본이므로 **실험 tokenizer는 최종 학습에 사용 금지**. 정식 F5 train-only 후보는 F4 완료 후 별도 생성한다.
- 자원: 실측96CPU/H2008/가용 RAM 약775GiB에서 CPU 실험 affinity0..63/RSS128GiB, GPU host64..95/RSS합256GiB, case900초와 파일시스템별 여유200GiB. 메인 cache128GiB/RSS384GiB/producer7개 SHA는 그대로. 새 프로세스는 fresh hardware와 계약을 저장하고 `parallel_job_support.py`에서 2초 감독, 기존30초 watcher에 등록했다.
- 사전 검증: 새 집중 단위검사2개 통과(비유한 reference/가중 상대오차, 1·2worker의 배치 경계/EOS/uint16 바이트 동일). micro 단일GPU와8GPU FSDP 실제 smoke 통과. 3B 및 CPU 전체 sweep 결과는 아직 미완료이며 각 run 상태/보고서를 확인한다.
- REF-TOK T01/T02 → benchmark_tokenizer_resources/test_tokenizer_resource_encoding. REF-TRAIN → validate_compile_long/test_compile_long_metrics. 완료 후 결과·메인 처리율·실측 최적값을 재검토한다. 공식 언어능력 benchmark/본학습은 미시작.

## 병행 후보 재탐색 (2026-11-06 11:10 UTC)

이번에는후보/의존성확인만수행,새job미기동. 메인full_v2 입력17,234,159/128,856,258(13.37%),producer7개SHA동일/감시running. H2008장memory.used0,CPUaffinity96,보수적RAMheadroom약787.5GiB. 큰원본재스캔이나실행중producer수정은피하고다음단계를앞당기는독립준비를우선한다.

1. GPU 수치·속도 검증 확대(권장Astra / High): attention eager+norm/casts후보를복수seed·점진적sequence확대로검증하고8GPU FSDP로확장. 현재수치harness는seq32고정이므로새격리runner에서구현해야하며seq4096 profile을수치검증완료로재사용하지않는다. 긴입력의VRAMpeak를측정한뒤장치별사례/8GPU순서를정한다. logits/loss/gradient/update·finite·처리량과메모리를함께보고하고생산채택은후속판단.
2. F5/F6 CPU 성능 사전측정(권장Sol / High): 별도고정실험표본·실험tokenizer경로에서BPE Rayon8/16/32/64 및tokenization worker4/8/16비교. 전체소요시간·RAM·입출력동일성과메인처리율저하여부를확인. 현재split미완료이므로최종train-only65536후보나전체tokenization은시작하지않는다. worker/Rayon총합은CPU96예산안에서조합한다.
3. F4 완료 판정·후속보고 준비(권장Sol / High): 완료된작은fixture로source별train/eval비율·그룹수/최대component·row coverage/교차검사증명·내용ID를묶는별도reporter를준비한다. 실제전체DB의큰집계는닫힌DB/완료후수행,기존검증을중복전수실행하지않도록증명재사용범위를명시한다. 미완료/분포이상시F5로넘기지않는다.

추천병행:1(GPU)+2(CPU),3은짧은구현/fixture검증으로먼저정리해도된다. 현재데이터작업을재시작하는최적화후보는이미처리한진척을잃으므로현시점우선순위에서낮춘다. 별도공식benchmark·본학습은각기존gate유지,서브에이전트위임을뜻하지않음.

## 성능 우선 재기동 갱신 (2026-11-06)

실제전체split은f4_full_v2(cache128GiB/RSS384GiB/hash32)로실행중. 이전v1은DB0bytes에서중지·보존. 실제SHA32worker및캐시3설정/300만논리동일·100만종단발행2.09배실측·115회귀+후속집중검사통과. 세부예산/현재카운트는STATUS최상단. 단일writer ingest의CPU/decode병목은다음실측대상,진행중코드고정유지. GPU수치후보는여전히생산채택pending.

## F4 로컬 발행·복구와 GPU 수치 진단 병행 (2026-11-06)

실행 갱신: 300만검사/발행·복구112tests/100만실증·전체행논리대조/GPU2case완료. 실제전체split runs/f4_full_v1 기동. 다음세부상태는STATUS최상단참조.

사용자 실행 승인에 따라 다음을 수행한다. 개발 권장: 데이터 Sol / High, 모델 수치 검토 Astra / High. 서브에이전트 없이 독립 서버 프로세스를 병행한다.

1. 기존 NFS 300만 synthetic split은 코드/설정 그대로 유지하고 완료 증명·실측 peak를 확인한다.
2. 새 local split 실행기: 고정 source/policy/code 계약, 단일 writer/프로세스 lock, 두 파일시스템 공간 예산, 닫힌 DB의 SHA 검증 복사, 같은 NFS 디렉터리의 원자적 rename, 발행 후 실제 consumer 검증. 중간 완료 표시를 금지한다.
3. 강제 종료 지점별 재개·기존 출력 충돌·내용 변경 거부·로컬 디렉터리 유실 복구를 실제 테스트한다. 로컬 ingest 중 컨테이너 유실은 영구 원본에서 재생성하며, 완료된 영구 staging/최종본이 있으면 이를 검증해 사용한다. 미완료 로컬 DB의 중간 백업은 이번 버전에 포함하지 않으며 재생성 시간 비용을 명시한다.
4. 기존 300만 결과 및 새 발행 검증이 통과하면 실제 128,856,258행 split의 시간/RSS/DB+저널/영구 복사 예산을 확정하여 새 출력으로 기동하고 30초 감시에 등록한다. 실제 완료 후 source 분포·큰 component와 교차0을 확인한다.
5. 독립 GPU 0/1에서 eager RMSNorm+cast 보존 기준에 attention 또는 MLP graph break를 추가한 3B/seq32 counterfactual 2개를 병행한다. case당900초, OMP2/compile2. 생산 모델 미변경, 공식 benchmark와 본학습 제외. 완료는 원인 진단이며 8GPU/long-context/다중 seed 채택 검증은 후속이다.
6. source split 완료 후에만 train-only 65536 raw tokenizer 후보를 시작한다. 기동 전 자원 재측정, 원자료 보존, 단계 종료 시 STATUS·이벤트 갱신.

# 중복 제거와 병행할 개발 계획

2026-11-06 F2 실행결과: full코드/웹raw·fence join완료0match,100만split64/512MiB비교·100만행동일(22m17s→7m42s),표본quota보완/105tests·6000표본진단완료. 범위명시한정책7조건확정후F3원본SHA재확인/새ready생성진행. F3완료후300만/cache512MiB/3600초/RSS4GiB/case8GiB/free200GiB검증추가,실제전체split은그결과로예산확정. STATUS최상단참조.

## F1 완료 후 F2 실행 계획 확정 (2026-11-06 08:39 UTC)

사용자지시로기존계획을실제실행설정으로구체화했다. 실행root runs/f2_execution_v1,plan/frame/runner_contract 고정. 개발권장Sol / High,현재Sol/선택effort확인불가. 큰I/O작업은하나씩순차실행한다.

1. **전체감사지문join:** 완료full report/certificate를검증하고726지문파일SHA/schema/행수를읽는다. 코드2,148,459개 vs 비코드126,707,799개 전문raw exact와closed fence를집계. 코드index상한300만/RSS8GiB/처리1800초·외부2200초. full_source_coverage=true와모든행처리가완료조건. partial이면후속정책통과시키지않는다. 원문231GiB재스캔/삭제없음.
2. **100만synthetic split:** 감사종료와freshfree검사를확인한후새scale디렉터리사용. 100만행,cache64MiB,1800초/RSS4GiB/case8GiB/free200GiB. DB검증·rowcoverage·group교차0와크기/시간/최대RSS/중단회복증거를확인. 필요시에만300만으로확대하고작은합성문서의용량을실제1.29억건보증으로외삽하지않는다.
3. **웹확대표본:** FW2/HPLT/영어각2000문서목표,seed1234,source별최대4파일×2rowgroups,총24groups/charged2GiB/저장128MiB/300초. 저장view최대4096자,유사검사view2048자/후보50000/각180초,threshold0.8/0.9/0.95. frame은완료candidate의해당source전체파일목록에서고정seed로고른다. 실제길이분포/잘림/후보범위·한도를보고하며complete_for_defined_frame을전체대표성/무중복으로표시하지않는다. cap/time partial은보존하고통과로확대하지않는다.
4. **품질근거갱신:** content_audit는full완료증명으로passed연결. 코드교차/near범위및추가조치,seed1234/eval fraction0.01초안,한/영holdout목표,ko50/en35/code+math15 byte초안의여섯source세부비중을실측근거로검토한다. 아직근거없는최종결정은pending으로남긴다. readiness보고서생성종료와실제passed구분.
5. **F3 진입조건:** 필요한품질정책모두명시적판정/내용증거일치뒤별도audited-ready를생성하고실제split→train-only tokenizer로진행. 이번3개evidence job완료만으로ready를올리지않는다. 본학습/자료덮어쓰기/외부업로드기존확인규칙유지.

공식평가의오염검사범위를결정할시점이다. 공식task원본/revision/schema가준비되지않아해당범위는미검사로유지한다. 사용자선택의별도언어능력benchmark점수는기동하지않는다. GPUcompile잔여수치문제도이번데이터작업의조건과구분한다.

운영: scripts/run_f2_checks.py가단계별timeout/fresh공간검사/stateheartbeat/child log를기록한다. parentstate5초갱신,독립job registry로30초감시·오류/종료보고. 전체summary/.done은측정완료증명이며qualitypending. 실행중도구/plan/frame변경시새경로사용.

2026-11-06 08:27 UTC F1 전체감사726/726·128,856,258문서완료,완료검토확인. 현재실행중큰I/O없음. 다음전체지문join→100만split→F2정책은기동준비상태이며아직미실행. 기존실행중감사보호규칙은해당감사실행기간기록이다. STATUS최상단참조.

2026-11-06 추천1·2실행완료: storage_budget/preflight 예산도구및독립job등록·30초감시통합,전체104tests통과. 실제중앙가정추가522GiB는맞지만stress1006GiB+reserve200은초과. 최종tokenizer/split실측후재계산필요. 신규watcher163709·독립7job등록,main감사유지. 공식평가/SFT후보는미착수. STATUS/RUNBOOK참조.

## 수치 분석 외 독립 병행 후보 (2026-11-06 07:54 UTC)

새로 확인한 구현 공백 기준이다. 이번에는 재탐색/계획 기록만 했고 아래 작업은 기동하지 않았다. 감사/모델 수치 검증의 완료를 대신하지 않는다.

| 우선순위 | 작업 | 확인 근거 / 지금 가능한 범위 | 완료 조건 | 권장 |
|---|---|---|---|---|
| 1 | 토큰화·split·checkpoint 디스크 예산 계산기 | preflight.py는 고정1.5TiB 예상, measure_split은case만감시. 완료audit/pilot receipt와기존작은tokenizer/scale 결과로추정범위·peak 동시보관량·200GiB reserve계산. 대량원문읽기없음 | 입력범위/가정/추정값과실측구분,최종tokenizer계측후갱신,예산초과시생성전정지조건. 기존raw/clean삭제계획없음 | Sol / High |
| 2 | 독립 진단 job 감시 등록 통합 | watch_data_pipeline.py는source_audit_job.json만등록소비. 최근GPU진단은각각state와임시30초watcher로분리. 여러독립job의pid/state/report/.done등록방식과이벤트통합준비 | 30초로컬감시,오류·정체·측정완료·needs_review구분,프로세스종료를통과로간주하지않음. 기존main감시유지/회귀검증 | Sol / High |
| 3 | 공식 평가 입력 adapter·오염검사 입력 준비 | eval/multiple_choice.py는versioned local schema까지만지원. 공식task의원본schema/revision/license/label/scoring를일차출처로확인하고작은fixture변환/지문입력설계 | 출처/변환/정답인덱스검사·평가원본식별,미확보task pending. 실제모델점수와별도이며학습checkpoint불필요 | Astra / High |
| 4 | SFT 대화 전처리·assistant label 경로 | sft/미구현. REF-SFT S01/S02/T02/E02 따라작은합성대화로system/다중턴/코드보존,assistant-only/-100/shift1회/EOS/잘림/캐시준비 | fixture label/token 경계검증,최종tokenizer 연결과SFT학습은후속. Base의선행조건으로만들지않음 | 데이터 Sol / High,loss검토 Astra / High |

추천1→2를메인감사와병행하고3은별도평가준비로진행한다. 4는후순위다. 이목록은기존F2정책보고서/지문join/후보누락검증을다시만드는작업이아니다. 100만split/전수join/원문표본확대는감사종료후큰I/O하나씩진행한다. 서브에이전트위임을포함하지않는다.

2026-11-06 추가진단완료: FP32차이는작고bf16 fusion/중간cast의기여를SwiGLU분리실험으로확인. 실제optimizer그룹적용및full3B후보3개비교완료. 최선norm+casts update10.74%로채택pending. 다음은잔여attention/projection/residual/backward 분해(Astra / High), 이후FSDP8/seq4096/여러seed. STATUS최상단 참조.

2026-11-06 실행 결과: 후보1 수치대조4case 실행완료(micro만제한통과,1B/3B/8GPU needs_review), 후보2 계측완료(forward/backward95.15%,H2D0.039%), 후보3 현재candidate 품질근거/후속준비완료(5조건pending). 후보4~2M batch는1의검토결과에따라보류. 다음은FP32/bf16·층별/optimizer 차이 분해이며 Astra / High. STATUS 최상단과 runs/compile_pair_v1/analysis.json 참조.

## 벤치마크 이후 병행 후보 재탐색 (2026-11-06 07:12 UTC)

점검 결과: main full 감사 실행 중, GPU compute process 없음. 기존 감사 dependency4개 hash 동일, wrapper/python/30초 watcher 생존, 새 오류/OOM 없음. 이번에는 후보/계획 확인이며 신규 GPU/개발 작업은 기동하지 않았다.

| 우선순위 | 작업/근거 | 범위·완료 조건 | 권장 모델/effort |
|---|---|---|---|
| 1 | eager/compile 전체 모델 수치 대조. 3B 40step loss 차이 원인 미확정 | 동일 초기 가중치·입력·RNG·optimizer state로 micro→1B/3B 제한 case. logits/loss/전체 gradient/optimizer1step 오차를 보고, FP32 기준·bf16 eager 반복 변동과 비교. 단일GPU 경로 후 FSDP 경로 확인. 실패 원인을 숨기는 tolerance 확대 금지. 원인/채택 조건 보고 | Astra / High |
| 2 | 학습 step 병목 profiling. 입력전송/finite 검사/CE 비용은 아직 추정 | GPU job 하나씩, 작은 합성 입력·몇 step·출력 trace 상한/timeout. H2D·forward/backward·gradient 검사·clip·optimizer·collective의 시간/메모리 근거. trace 자체 overhead와 비프로파일 처리량 구분, 측정 병목에만 최적화 후보 제시. all-rank 오류 검사는 유지 | Astra / High |
| 3 | F2 품질 결정 기록·후속 실행 준비 | 기존 보고서만 읽어 유사/코드교차 범위·그룹분리/eval 비율·tokenizer byte 구성의 근거/미결 항목 정리, pending 유지·검사 코드 fixture 준비. 감사 종료 후 full join/ready에 연결할 실행 순서 고정. 미완료 full 증명을 대체하지 않음 | Sol / High |
| 4 | 실제 예정 global batch 규모의 합성 짧은 검증 | 1의 수치 문제 판단 후 실제 설정 후보(~2M tokens/step)로 accumulation·메모리·처리량 확인. 기존131072/step 결과를 본학습 규모 결과로 외삽하지 않음. bounded synthetic·checkpoint쓰기 최소화, 본학습 아님 | Astra / High |

추천 순서1→2, 그 사이3의 CPU 준비 가능. 4는1 결과에 종속된다. 한 번에 GPU job1개, 큰I/O source 감사1개를 유지한다. 실제100만 split·전체지문join·원문확대표본은 감사 종료 후 순차 실행. SFT/export는 현재 base 경로의 병목을 해소하지 않아 후순위. 병행은 서버 main 작업과 독립 개발/검증이며 서브에이전트 위임을 뜻하지 않는다. 기존 실행 계획의 권장값을 적용하며 자동 모델 변경은 없다.

2026-11-06 사용자 추가 지시: GPU 벤치마크 실행 및 최적화 후보 검토를 승인했다. 합성 bf16 수치 확인→tiny→1B/3B compile·micro batch 비교를 진행한다. 공식 언어능력 task는 tokenizer/학습 checkpoint 준비 후 별도이며, 실제 source 감사와 본학습은 구분한다. 실행 결과는 STATUS 최상단과 runs/gpu_benchmark_v3를 따른다.

## 사용자 선택 — 벤치마크 별도 진행 (2026-11-06)

사용자는 공식 벤치마크를 별도 진행하기로 했다. 현재 병행 범위는 감사 지문 소비/교차검사 도구 → 유사중복 후보 누락 검증 → 그룹 분리 확장 준비이며, 공식 benchmark 원본 다운로드·adapter·점수 실행과 별도 GPU 성능 benchmark는 시작하지 않는다. F2 데이터 품질 확정에서 benchmark 오염검사 범위를 정할 때, F7 실제 모델 평가 진입 전에 대화 중 필요한 시점을 알린다. 별도 benchmark가 실제 수행됐다고 기록하거나 과거/합성 점수를 공식 결과로 표시하지 않는다. 필수 평가 조건은 pending/분리해서 유지하며 과거 계획의 benchmark 동시 착수 문구보다 이 선택이 우선한다.


## 병행 1→2→3 실행 결과 (2026-11-06)

3건 모두 구현·작은 검증 완료, 전체 CPU89개 통과. 실제 감사는 유지한다. 적용 파일·검사·한계·현재 진행 수치는 [STATUS](STATUS.md) 최상단을 따른다.

| 작업 | 실제 증거 | 아직 남은 실제 적용 |
|---|---|---|
| 감사 지문 join | parallel_audit_overlap_pilot_v2: 코드33,963·웹3,575,986·fence443, whole/fence0. 명시적 pilot/full coverage=false | full 감사 후 전체 지문 join, 검사 밖 inline/부분/변형 범위 결정 |
| 후보 누락 검증 | parallel_similarity_recall_v1: 실제96+합성40, 전수7,168쌍. 합성 양성 누락0; 실제 양성0이므로 recall=null | 확대 실제 strata 표본·후보 재현율 한계와 품질 조치 결정 |
| split 확장 준비 | parallel_split_scale_ready_v3: 합성1,000건 검증. 최대300만 설정/상한·중단/복구·SHA 검증, SQLite cache64MiB | 감사 후100만 실제 측정, 필요 시300만. 현재100만 입력/DB 미생성 |

벤치마크는 사용자 별도 진행 선택에 따라 후속 알림 대상으로 유지한다. 아래06:24 후보 표는 실행 전 기록이며4/5의 benchmark 동시 착수를 뜻하지 않는다. active audit의 코드·입력은 변경하지 않았다.

## 감사 실행 중 병행 대상 재확인 (2026-11-06 06:24 UTC)

당시 상태: 후보 확인/우선순위 갱신만 수행. 1~3은 이후 위 실행 결과처럼 구현/소량 검증을 마쳤다. 현재 full 감사는 유지한다. audit/source_inventory/data_contracts/code-math fence helper 및 candidate/원문은 실행 종료 전 고정한다. 현재 감사 dependency SHA가 계약과 일치함을 재확인했다.

| 순서 | 병행 후보 | 지금 가능한 범위와 수정 예정 파일 | 완료 조건 / 실제 실행 시점 | 모델 / effort |
|---|---|---|---|---|
| 1 | 감사 지문 기반 코드↔웹 교차검사 소비기 | 새 data/probe_audit_overlap.py와 tests. raw/fence fingerprint를 읽어 join하고 source/file/row로 근거 기록. 원문 재스캔 불필요 | 작은 fixture·완료 pilot 지문에서 hash/행 coverage·부분 scope 거부/표시 검증. 전체 교차 집계는 full 감사 종료 후 | Sol / High |
| 2 | 유사중복 후보의 누락 검증 | 새 data/probe_similarity_recall.py와 tests. 보존된 N11 작은 표본 및 합성 변형에서 모든 source간 pair와 후보 생성 결과 비교 | threshold별 실제 양성/탐지/누락·분모0 처리. 표본 내부 관측만 보고하고 전역 재현율로 확대하지 않음. CPU/작은 저장 표본만 사용 | Sol / High |
| 3 | 그룹 분리100만 확장 실행 준비 | scripts/measure_split.py/data/split_manifest.py의 제한·진행·자원 측정·finalize 크기/중단 복구를 보완, 작은 fixture로 검증 | 현재 measure_split은 max100000이라100만을 실행할 수 없음. schema/config/cache/journal/timeout 계획과 작은 회귀를 먼저 준비. 큰 synthetic DB 측정은 감사와 I/O 경쟁을 피해서 후속 실행 | Sol / High |
| 4 | 공식 benchmark 원본 adapter/오염검사 입력 | eval 새 adapter와 tests, 공식 schema/revision/license/label/template/scoring 근거 확인 및 작은 fixture 변환 | 현재 multiple_choice는 local 공통schema까지. 대량 다운로드/실제 점수는 이후, 필수 task 누락을 성공으로 처리하지 않음 | Astra / High |
| 5 | GPU 수치 검증·성능 캐시 식별 보완 | 새 제한된 CUDA bf16/장문 RoPE 비교 script·tests, benchmark_gpu.py의 code/input/environment/result hash 계약 | GPU compute process 없음 확인. 모델 본체 변경 없이 작은 GPU job1개/timeout/lock 사용 가능. 큰1B/3B 측정·본학습은 후속 | Astra / High |

당시 추천 순서는 **1→2→3의 CPU 개발·작은 검증**이었다. 현재1~3 완료,4/5 중 benchmark는 사용자 별도 진행 선택을 따른다. source audit의 큰 읽기/지문 쓰기와 함께 실제100만 DB/전수교차/대량 token화를 기동하지 않는다. 별도 합성1M용 데이터 생성·DB I/O를 독립 CPU 개발과 혼동하지 않는다. GPU가 비어 있어도 기존 검증을 이유 없이 반복하지 않는다.

사전 자원 조회: 감사 약1CPU 미만·RSS약247MiB, I/O full avg60약21.7%, 디스크 약943~944GiB, GPU process 없음. 따라서 새 코드/작은 fixture 및 제한된 GPU 계산은 가능하고 큰 I/O 추가는 후속으로 둔다. 모델 전환은 작업 진입 시 알림, 주 세션 자동 변경/agent 자동 위임은 포함하지 않는다.


## 현재 실행 — F1 실제 내용 감사

2026-11-06 F0 구현/CPU81개 검증 후 실제6파일 pilot 통과, runs/source_audit_full_v1에서726파일 전체 감사를 실행 중이다. 대량I/O 작업은 이것 하나만 사용한다. 감사가 끝날 때까지 source_audit/source_inventory/data_contracts/code-math fence helper와 candidate/입력은 고정한다. split 확장 준비/작은 검증은 완료했고 공식 task adapter는 사용자 별도 작업으로 유지한다. 실제 heavy scan은 병행 기동하지 않는다. 최신 PID/진행/ETA는 STATUS를 따른다.


## 현재 후속 작업 안내 — 정확중복 완료 이후

2026-11-06: exact_v1 662/662 완료. 다음 전체 실행 계획은 [WORKFLOW 최상단](WORKFLOW.md)의 F0~F10으로 정리했다. 우선 감사의 파일별 재개/진행·candidate→ready 내용 증명 연결을 보완하고, source별 pilot 후 726파일 실제 내용 감사를 수행할 계획이다. 현재는 계획 작성 완료/신규 실행 미착수다.

감사 중 병행 후보는 synthetic 그룹 분리100만 문서 확장 준비, 공식 benchmark schema/오염검사 범위 준비, 필요 시 SFT label fixture 준비다. 큰 I/O scan은 하나, GPU job은 하나씩 진행하며 agent 자동 위임을 포함하지 않는다. F0~F6은 Sol / High, 실제 모델·평가 경로는 Astra / High, 3B 사전 검토는 Astra / xhigh. 아래 과거 '진행 중 exact_v1'은 당시 계획/기록이다.


## 확정 작업 순서 — 1·2·3 / N9→N10→N11 (2026-11-06)

실행 결과(2026-11-06): **N9/N10/N11 도구 구현·검증 완료, 실제 데이터 readiness는 pending**. 전체 CPU 74개 통과(logs/tests_n9_n11_final_v3.log). N9 실제 v3 보고서에 N11 증거를 연결했다. N10 합성 split/tokenizer/holdout 증명과 native 평가 경로 통과. N11 v1은 읽기 상한에 걸린 partial, v2는 네 소스 64건씩 256문서의 작은 frame 추출 완료. 웹 후보22쌍 중 threshold0.8 이상0, 코드↔웹 whole exact0, eligible fence0(블록 판단 불가). source ready·전체 split·최종 tokenizer·전체 중복/품질 합격과 구분한다. 상세 적용 파일·한계는 STATUS 참조.

사용자 요청: 병행 작업 1·2·3의 순서별 실행 계획 작성. 아래는 실행에 사용한 상세 계획이며, 실제 구현/소량 검증 결과는 위 요약 및 STATUS를 따른다. 기존 N1~N8 결과와 실행 중 exact_v1은 유지한다.

### 순서와 완료의 의미

| 순서 | 작업 | 지금 끝낼 범위 | 후속 연결 | 권장 개발 모델 / effort |
|---|---|---|---|---|
| 0 | 실행 전 점검 | 상태 이벤트·데이터 PID·OOM/메모리/디스크·입출력 압력 확인, 새 출력 경로 지정 | 기존 dedup과 독립된 작업만 시작 | Sol / High |
| 1 / N9 | 데이터 품질 근거·완료 집계 | 증거 schema·집계/판정기 구현, 작은 fixture 검증, 진행 중 데이터의 pending 보고서 | 도구 검증 완료 후 N10으로 이동. 실제 품질 최종 통과는 N11/데이터 완료 후 | Sol / High |
| 2 / N10 | 분리 결과를 tokenizer·평가 입력에 연결 | train-only tokenizer 표본, eval-only 원문 JSONL 추출·증명 확인, 합성 통합 검증 | 실제 source/split이 없더라도 도구 검증 완료 후 N11로 이동 | Sol / High; PPL/loss/채점 변경은 Astra / High |
| 3 / N11 | 유사중복·코드↔웹 제한 표본 진단 | 합성 양성/음성 검증 → 고정 완료 출력의 제한 표본 추출/검사 → 범위와 판단 근거 기록 | N9에 근거를 재입력하여 남은 검사/추가 처리 여부 판정 | Sol / High |
| 4 / 실제 적용 | 최종 입력 확정 및 실제 연결 | dedup 전체 완료 검증·품질 근거 통합 → ready source → 실제 그룹 분리 → N10 표본/holdout → raw 후보 학습/검사 | 대량 토큰화·실제 GPU 검증으로 이어짐 | 데이터 Sol / High, 실제 모델 GPU 검증 Astra / High |

**1→2→3은 개발/검증 순서다.** N9의 `pending`이 N10/N11의 독립 코드 작업을 막지 않는다. N10의 실제 적용은 ready와 split 증명이 있어야 하며, 이 요구를 임시로 해제하지 않는다. N11이 추가 제거를 필요로 한다고 판단되면 새 source snapshot에서 분리·표본을 다시 만든다. 이전 snapshot 결과를 재사용하지 않는다.

### 1 / N9 — 품질 근거와 완료 집계

목적: 최종 입력으로 넘어가기 위해 무엇이 끝났고 무엇이 부족한지 파일 단위 증거로 판정한다. 구조 검사 성공·계획 문서·과거 acceptance 상태를 품질 완료로 오인하지 않는다.

예정 파일: `data/quality_report.py`, `scripts/check_data_readiness.py`, `tests/test_data_readiness.py`. 기존 `scripts/prepare_source_manifest.py`, `scripts/validate_source_manifest.py`, `common/data_contracts.py`는 필요한 인터페이스만 연결하며 기존 ready-only 소비 경로를 유지한다. REF-DATA D03 / REF-TOK T02 적용.

작업 순서:
1. 증거 입력 schema를 정의한다. source/revision/선택 파일, 처리 정책, shard별 완료 증거, 검사 보고서, 분리/표본 정책의 식별자를 묶는다. 웹 5소스와 코드 1소스를 구분하고 기존 코드↔수학 완료 결과를 재사용한다.
2. dedup 완료 shard의 보고서를 읽어 source별 input/kept/duplicates를 집계한다. 키 중복·음수·`input != kept + duplicates`·누락/초과 파일·정책 불일치를 거부한다. 반복 로그를 합산하지 않으며 진행 중 DB는 열지 않는다.
3. 필수 조건 목록을 판정한다: 전체 exact_v1 완료/집계, 코드 처리, 유사중복 판단 근거, 코드 교차검사 범위 결정, repo/문서 분리 정책, tokenizer byte 구성. 각 조건의 `passed/pending/failed`, 근거 파일 hash와 남은 작업을 출력한다.
4. candidate inventory의 사전 점검과 ready-only 소비 검사를 구분한다. draft를 감사해 보고서를 만들 수 있어도 학습기·토큰화의 ready 조건을 해제하지 않는다. 구조 무결성·본문 전체 hash/읽기는 실제 완료 후 별도 검증 증거로 연결한다. 메타데이터/기존 기록 확인과 실제 본문 읽기 검증을 명시적으로 구분한다.
5. fixture 검증 후 현재 진행 snapshot에 대해 메타데이터/완료 보고서 중심의 가벼운 상태 보고서를 만든다. 진행 중이면 `pending`으로 종료하고 전체 통과 증명은 만들지 않는다.

완료 조건:
- 정상 집계, 미완료 pending, 증거 누락, 같은 경로의 변경 보고서, input/kept/duplicate 불일치, source 정책/revision 불일치 사례를 검증한다.
- 모든 필수 항목이 근거를 갖고 판정되며 pending/failed를 통과로 바꾸지 않는다.
- `runs/n9_readiness_<새이름>/report.json`에 도구/설정/증거 내용 식별·집계·조건별 상태·다음 조치를 남긴다. 도구 실행 종료와 실제 데이터 ready 합격은 별도 필드로 기록한다. 동일 계약 재사용은 내용 증명으로 확인한다.

실제 source ready는 N9 도구가 구현됐다는 이유로 변경하지 않는다. N11 근거와 전체 데이터 검증을 받은 뒤 최종 재판정한다.

### 2 / N10 — train-only tokenizer 표본과 eval-only 평가 입력

목적: 선택한 split의 문서가 올바른 소비 경로에만 들어가며, 입력의 원본 위치와 본문을 추적할 수 있게 한다. 현재 최종 tokenizer는 미학습이므로 예방적 연결 보완이다.

예정 파일: `data/split_inputs.py`(공통 선택/추출), `tokenizer/train_tokenizer.py`, `eval/prepare_holdout.py`, `eval/run.py`의 입력 증명 확인 부분, `tests/test_split_inputs.py`. 기존 `data/split_manifest.py:SplitSelection`을 재사용한다. REF-DATA / REF-TOK T01,T02 / REF-EVAL E01~E03 적용.

작업 순서:
1. source snapshot·file 내용 hash·split 계약이 일치하는 선택기를 준비한다. 문서는 source/file/row/body SHA256으로 추적하고 코드에는 repo provenance를 유지한다. 웹에 없는 raw row 정보는 만들지 않는다.
2. tokenizer 학습 CLI에 split manifest 연결을 추가한다. 최종 경로에서는 train row만 전달하고 eval row는 표본 선택 전에 제외한다. raw 정규화 기본을 유지한다. 실험/legacy 입력 경로가 필요하면 명시적 모드와 증거를 구분한다.
3. seed·파일/row group 추출 frame·문서 선택 순서·소스별 UTF8 byte 예산·길이 제한을 기록한다. 전체 row group을 Python list로 모으는 부분은 제한된 batch 순회로 바꾼다. 목표 byte 부족·없는 source·비어 있는 train 표본을 조용히 무시하지 않는다.
4. 실제 학습에 넣는 표본의 file/row·원문 hash·잘림 정보·사용 텍스트 hash를 함께 보존한다. 후보 계약에 split ID/표본 내용 SHA/추출 코드·설정 식별을 추가하고 같은 vocab/mtime만으로 재사용하지 않는다.
5. eval row에서만 ko/en 원문 JSONL을 추출한다. 초기 제한은 한/영 각각 정한 문서/byte 예산으로 하고, 한국어/영어 source mapping을 명시한다. 원문 텍스트를 보존하고 EOS 추가는 기존 평가기에서 한 번만 한다. 코드·수학을 ko/en으로 억지 분류하지 않는다.
6. holdout 입력 증명에 source/split/file/row/body hash·JSONL hash·추출 설정을 남긴다. eval/run.py에서 실제 holdout 경로는 이 증명을 확인하도록 연결한다. 수동 fixture/프롬프트 입력은 provenance 수준을 구분하고 검증된 실제 holdout이라고 표시하지 않는다.

완료 조건:
- 작은 ready fixture에서 split 생성 → tokenizer iterator의 train 문서 선택 → eval JSONL 추출/읽기까지 한 번에 확인한다.
- eval 문서가 tokenizer iterator에 없고 train 문서가 holdout JSONL에 없으며, exact/repo 연결 그룹의 교차도 없는지 확인한다.
- seed 재현, byte 통계, eval-only shard, train/eval 부족, 변형 source/본문/split, 증명 없는/변경된 JSONL 거부를 검증한다. 원자적 저장·중단 흔적과 입력 변경의 재사용 규칙을 확인한다.
- `runs/n10_split_inputs_<새이름>/`에 합성 통합 결과와 계약/출력 증명을 보존한다. 임시 작은 tokenizer만 사용하며 실제 최종 65536 후보 합격을 뜻하지 않는다.

모델 알림: 추출·표본·계약 구현은 Sol / High. PPL 계산식·loss/shift 또는 점수 집계 자체를 바꿀 사유가 생기면 `모델 전환 권장: Astra / High — 평가 계산 경로 검토`를 알리고 해당 범위를 별도 검증한다.

### 3 / N11 — 본문 유사중복·코드↔웹 제한 표본

목적: 정확중복 검사 밖의 위험과 추가 검사 필요성을 측정하고 N9의 정책 판단 근거를 만든다. 코드 내부 exact 0건·코드↔수학의 완료 범위는 이미 확인됐으므로 같은 전수 검사를 반복하지 않는다.

예정 파일: `data/probe_text_similarity.py`, `data/probe_code_web_overlap.py`, `data/sample_snapshot.py`, `tests/test_text_similarity.py`, `tests/test_code_web_overlap.py`. 기존 `data/probe_code_math_overlap.py:fenced_blocks` 등 보존 규칙을 재사용하되 결과 범위를 구분한다. REF-DATA D03 / 품질 ready 근거 적용.

작업 순서:
1. 고정된 완료 파일 목록을 추출 frame으로 만든다. 미완료/작성 중 파일을 제외하고 source/revision·경로·크기/mtime·선택 row·본문 hash를 기록한다. 추가 완료 shard를 같은 실행에 자동 추가하지 않는다. 실제 전체 source hash 검증 여부도 별도로 기록한다.
2. 웹 검사: 우선 FineWeb-2↔HPLT를 중심으로 여러 파일/row group에서 추출한다. 원문 exact와 별도의 본문 n-gram 유사도 후보를 구분하고, 후보 생성 뒤 실제 집합 유사도를 재계산한다. 분모·잘림·스킵·후보 상한에 걸린 수와 미검사 범위를 표시한다.
3. 코드↔웹 검사: 선택 코드 파일 전체↔웹 문서 전체의 raw exact, 코드 파일↔웹 fenced block의 경계 CR/LF 제외 exact를 먼저 비교한다. 부분/변형 코드를 추가하면 별도 지표/정책으로 보고한다. 대소문자·들여쓰기·Unicode를 바꾼 결과를 같은 exact 기준으로 취급하지 않는다.
4. 합성 fixture로 복제·부분 포함·웹 사소한 변형·무관 문서, 코드 들여쓰기/대소문자/분해형 Unicode·미닫힌 fence를 확인한다. 웹 후보 threshold는 실제 실행 전에 기록하며 임계값별 진단 결과를 비교한다. 실패한 검사를 통과시키기 위해 사후 threshold를 바꾸지 않는다.
5. 작은 실제 표본 1차 검사 후 후보 예시/source pair별 집계/조건별 탐지 범위를 남긴다. 0건이라도 전체 자료의 중복 없음으로 결론짓지 않는다. 표본 frame·길이·후보 생성의 검출 한계와 함께 추가 검사/비중 조정/현 정책 유지의 근거를 작성한다.
6. N9 증거 목록에 연결해 보고서를 다시 생성한다. 추가 전수 검사나 정제가 필요하면 해당 조건은 pending으로 남겨 실제 후속 단계만 대기시킨다.

자원·상한:
- 초기 실제 표본: source당 최대 5,000문서, 저장 본문 전체 최대 128MiB, CPU threads/workers 2 이하. 파일과 row group을 여러 곳에서 고정 seed로 선택한다. 전체 source 대표성을 보장한다고 표현하지 않는다.
- 저장 byte뿐 아니라 읽을 row group 수·읽기 byte 예산도 설정하고 기록한다. 작은 표본 때문에 원본 전체를 읽거나 full hash를 다시 계산하지 않는다.
- 문서 길이/사용 구간 상한을 명시한다. 잘린 표본의 유사도를 전체 파일 exact 결과로 보고하지 않는다. whole-document raw hash와 검사 구간 hash를 구분한다.
- 후보 pair 수·비교량·실행 시간 상한(최초 비교 job 10분)을 둔다. 상한 도달은 중단/부분 결과로 표시하며 전체 검사 완료 마커를 만들지 않는다.
- 출력은 `data/samples/n11_<새이름>/`, 코드 실행 증거는 `runs/n11_<새이름>/`에 분리한다. 기존 raw/clean/활성 exact_v1 index는 읽기 외 변경하지 않는다.

완료 조건: 합성 양성/음성 통과, 고정 표본의 내용 식별/재현, 상한과 실제 범위 보고, source 변경/결과 변조 재사용 거부, 실제 제한 표본 결과와 다음 판단 기록. 도구 검증·표본 검사 완료·전체 정책 판정은 각각 별도 상태다.

### 공통 검증·알림·중단/재개

- 각 단계 시작에 현재 작업/권장 Sol / High/이유를 알리고 같은 단계 내 반복하지 않는다. 1→2→3에서 모델 변경은 기본적으로 필요 없다. 현재 선택 effort는 확인 불가이므로 High 선택 완료로 표시하지 않는다.
- 단계별 필요한 회귀를 먼저 수행하고 변경 범위의 검증이 통과한 후 다음 단계로 이동한다. N11까지 구현한 뒤 영향 범위를 포함한 전체 CPU 회귀를 한 번 수행한다. 기존 62개 통과 기록을 새 코드 통과로 재사용하지 않는다.
- 계획의 완료 조건을 실제 파일·실행 로그·contract hash로 확인하고 STATUS에 적용 파일/REF/실제 입력/검증 결과/남은 실제 조건/다음 모델을 기록한다.
- 시작·완료·오류·상한 도달·새 정체는 대화 중 보고 및 logs/pipeline_events.jsonl에 구분해 기록한다. 기존 서버 watcher 30초를 유지하며 검사마다 모델 호출이나 대화 종료 후 자동 메시지를 가정하지 않는다.
- 작업은 새 출력 경로에 저장하고 동일 계약의 완료 결과만 증명 검증 후 재사용한다. 중단된 결과는 원자적 저장/내용 검증으로 복구하거나 미완료로 보존한다. 다른 입력/정책 결과를 덮어쓰지 않는다.
- OOM·새 데이터 오류·I/O 경쟁 심화가 확인되면 추가 작업부터 중단/축소하고 기존 dedup의 상태를 먼저 점검한다. 중단 시 마지막 완료 경계·PID·로그·재개 명령을 남긴다.
- 이 계획에는 서브에이전트 자동 실행을 포함하지 않는다. 본학습·원자료 삭제/덮어쓰기·외부 업로드는 프로젝트의 별도 확인 규칙을 따른다.


## 재탐색한 다음 병행 후보 N9~N14 (2026-11-06 04:52 UTC)

상태: 코드·문서·실행 상태의 읽기 점검 및 후보 정리 완료. 아래 신규 개발/검증은 아직 착수하지 않았다. N1~N8은 완료 증거를 유지한다. 기존 exact_v1과 30초 감시는 계속된다. 병행은 기존 데이터 프로세스와 독립 개발의 병행이며 agent 위임 계획이 아니다.

이번에 확인한 구체 공백:
- scripts/prepare_source_manifest.py는 draft inventory만 작성한다. scripts/validate_source_manifest.py는 ready 구조 감사이고 품질 증거를 통합해 최종 준비 여부를 판정하는 기능은 없다.
- tokenizer/train_tokenizer.py의 sample_texts/CLI에는 split manifest 연결이 없다. 토큰화의 eval 제외와 별도로, tokenizer 학습 표본에서 eval 문서를 제외할 수 있도록 연결할 가치가 있다. 현재 최종 tokenizer는 미학습이므로 실제 오염이 발생했다고 판단하지 않는다.
- eval/run.py는 제공받은 ko/en JSONL을 읽으며 선택한 split의 실제 eval row라는 증명을 자체 확인하지 않는다. split에서 원본 row/hash를 따라 입력을 추출하는 도구가 아직 없다.
- data/probe_url_overlap.py는 URL 표본, data/probe_code_math_overlap.py는 exact/일부 fenced block 검사다. 웹 본문 유사중복과 코드↔웹의 제한 표본 검사는 아직 구현되지 않았다.
- eval/multiple_choice.py는 명시적 버전의 local 공통 schema까지다. 공식 벤치마크 원본 스키마를 변환하는 adapter는 없다.
- scripts/benchmark_gpu.py:65~67은 config 비교 후 .done/result.json을 재사용하며 코드·환경·결과 checksum의 내용 계약을 검사하지 않는다. 기존 결과를 변경하지 않고 새 harness 검증 시 이 부분을 먼저 보완한다.
- tests/test_model_reference.py의 독립 비교는 작은 CPU FP32/짧은 context다. 장문 위치의 RoPE·CUDA bf16 수치 비교는 미검증. sft/는 아직 없다.

| 우선순위 / 후보 | 지금 독립 준비할 범위 | 산출물·완료 조건 | 실제 적용 조건 | REF / 권장 모델 |
|---|---|---|---|---|
| 1 / N9 품질 증거·완료 집계 | 완료 shard 보고서/기존 overlap/정책 근거의 입력 schema와 판정기. 작은 fixture로 누락·내용 변경·집계 불일치 거부 | source별 검사/유지/제거 합계, snapshot별 근거 hash, 필수 조건별 passed/pending/failed 보고서. dedup 미완료는 pending | exact_v1 완료 및 품질 결정 후 최종 적용. 단순 구조 성공만으로 ready 통과시키지 않음 | REF-DATA/TOK / Sol / High |
| 2 / N10 split→tokenizer·평가 입력 연결 | SplitSelection 재사용, train row만 tokenizer 표본 선택, eval row만 ko/en JSONL 추출. 원본 file/row/body hash와 split 계약 기록 | 합성 입력에서 eval 문서가 tokenizer iterator에 없고 train 문서가 holdout JSONL에 없는지 검증. 입력 변경 거부, byte 구성/부족 표본 보고. 샘플에 든 본문 내용 식별 보완 | 최종 source ready + 실제 split 후 후보 tokenizer 학습/holdout 추출에 적용 | REF-DATA/TOK/EVAL / Sol / High, 평가 점수 경로 변경 시 Astra / High |
| 3 / N11 본문 유사중복·교차 표본 진단 | 우선 합성 변형 쌍과 여러 완료 파일/row group의 제한 표본. 웹 유사도와 코드 raw/fence 비교를 구분 | 추출 seed·포함 파일/row·잘림/한도·정책·일치 후보/검증 값. 작은 표본 결과를 전체 중복률/자동 제거율로 쓰지 않음 | 실제 최종 입력의 유사중복 추가 처리/비중 결정 근거로 활용. 완료 출력만 읽고 원본/활성 index는 변경하지 않음 | REF-DATA D03 / Sol / High |
| 4 / N12 실제 benchmark adapter | KMMLU/KoBEST/HAE-RAE 중 단순한 task부터 공식 schema/revision/license 확인 후 작은 fixture 변환. 정답 mapping·prompt·split·채점 규칙 명시 | 공식 근거와 task 버전, 원본→local schema 변환 회귀, 잘못된 라벨 거부·context/token 경계 검사. 기존 synthetic 점수를 실제 benchmark 점수로 표시하지 않음 | 실제 평가 전 dataset snapshot/선택한 template와 tokenizer/checkpoint가 준비돼야 함 | REF-EVAL E01~E03 / Astra / High |
| 5 / N13 GPU 측정 식별·장문 bf16 | 우선 benchmark 캐시의 code/input/environment/result 식별 검사. 이후 하나의 제한된 GPU job으로 작은 모델의 장문 RoPE·FP32/bf16 logits/loss/gradient 비교 | 계약 변경 시 예전 결과 재사용 거부, 같은 계약 증명 확인. 장문 위치/설정/사전 허용오차/시간/메모리 결과 보존 | GPU 비점유/공유 잠금, 개발 소스 고정. 허용오차는 실행 전 정하고 실패 시 사후 완화하지 않음. 실제 1B/3B 성능은 별도 | REF-TRAIN/EVAL / Astra / High |
| 6 / N14 SFT 데이터 경로 선행 준비 | 학습 실행 없이 system/다중 턴 템플릿·assistant-only labels·shift·EOS/-100 처리 | S01/S02 fixture + 빈 응답/잘림/단답/코드/JSON 검사. 실제 tokenizer/template 계약은 후속 연결 | Base 품질 확인·실제 SFT 데이터와 학습 조건은 후속 단계 | REF-SFT S01/S02/T02/E02 / Sol / High, loss 검토 Astra / High |

추천 착수 순서: **N9 → N10 → N11 → N12 → N13**, N14는 사전학습 진입을 막는 항목이 해소된 뒤로 둔다. N9~N11은 Sol / High를 유지할 수 있다. N12/N13 진입 시 Astra / High 알림. 주 세션 모델 자동 변경이나 선택 effort 확인을 가정하지 않는다.

자원 계획: CPU 개발/fixture는 OMP threads=2 수준. N11 최초 실제 표본은 소스당 최대 5,000문서, 총 본문 128MiB 등 행·byte 상한을 모두 적용하고 여러 파일/row group을 포함한다. 고정된 추출 frame 밖의 대표성은 주장하지 않는다. 전체 파일 내용 hash/전수 유사중복/대량 split/전체 tokenizer 학습은 이번 소량 준비와 구분한다. GPU 검증은 하나만 실행하고 실제 큰 checkpoint 저장/학습 성능 sweep은 후속 조건에 맞춘다.

HF export(REF-EXPORT X01)는 native 평가가 있으므로 필요성이 생길 때 진행한다. 실제 1B/3B 성능 재측정·대량 교차 검사·본학습은 이 후보 점검에서 기동하지 않았다.


## 현재 추가 작업 N5~N8 결과 (2026-11-06)

사용자의 진행 지시에 따라 N5 정규화 비교(Sol / High) → N6 모델 수학 검증(Astra / High) → N7 SQLite 복구/제한 규모 측정(Sol / High) → N8 객관식 평가 준비(Astra / High)를 순차 수행했다. 별도 agent는 사용하지 않았다. 기존 exact_v1은 계속 진행한다.

- N5: 실제 소량 384/128 문서와 Unicode probe, 새 후보 기본 raw 결정. 최종 후보는 미학습.
- N6: 12가지 작은 FP32 구성에서 독립 logits/loss/전체 gradient 검증. 생산 모델 코드 변경 없음.
- N7: 7개 강제 종료 지점 복구 및 합성 1만/10만 건 측정. 10만 건 생성 116.76초·DB 약 78MiB. 실제 전체 적용은 대기.
- N8: 명시적 버전 local 객관식 adapter·채점·CLI 합성 검증. 실제 벤치마크 원본 연결은 대기.
- 전체 CPU 62개 통과: logs/tests_n5_n8_final.log. 상세 파일/REF/증거는 STATUS. 다음은 데이터 품질·입력 확정(Sol / High)이며 본학습 실행 승인은 별도다.

아래 N1~N4 표와 부분 재개 미구현 문구는 당시 기록이다. N7 builder v2의 동일 계약 SQLite 부분 재개는 이제 구현/검증됐고, GPU suite 자체의 부분 자동 재개는 여전히 미구현이다.


작성·실행 기록 반영: 2026-11-06. 작업 범위는 현재 3B Dense 프로젝트의 단계 2~7 준비다. 아래 상태는 합성 검증과 실제 데이터 검증을 구분한다. 현재 프로세스·상세 증거는 STATUS를 따른다.

## 다음 별도 작업 순서 — C2 합성 검증 이후

작성: 2026-11-06 03:53 UTC, 실행 결과 반영: 04:11 UTC. 아래 A~E 및 03:21 기록은 이전 계획과 완료 증거를 보존한다. N1~N4는 아래와 같이 구현·합성 검증을 수행했으며, 실제 데이터 적용은 다음 단계다.

| 작업 | 현재 결과 | 실제 적용 전 남은 조건 |
|---|---|---|
| N1 | 검사 CLI·다언어/특수토큰/내용 식별 검사 통과 | 코드 Unicode를 바꾸는 NFC는 needs_review, 실제 tokenizer 정책/품질 결정 |
| N2 | compile=true 8GPU 6개 사례 통과, 84개 텐서 최대차 0 | 실제 데이터·본학습 크기 검증 |
| N3 | native DCP→한/영 PPL/생성→JSON CLI CPU 검증 통과 | 확정 holdout JSONL·실제 checkpoint 평가 |
| N4 | SQLite 그룹 분리·구조 감사·토큰화 split/mix 연결 검증 통과 | 대량 처리 비용·실제 품질/분리 정책 확정 |

전체 CPU 52개 통과: logs/tests_n1_n4_final.log. GPU: runs/n2_compile_v1/summary.json. 아래 상세 항목은 요구사항과 실행 순서를 보존하며, 잔여 조건은 STATUS를 우선한다. 새 harness의 중단 suite 부분 재개 및 대량 SQLite split build 부분 재개는 미구현이며 이전 증거를 보존하고 새 출력 경로를 사용한다.

출발점: exact_v1 535/662 진행, 30초 감시 유지. CPU 43개·C2 compile=false 8GPU 6개 사례 통과. 최종 입력은 draft이고 실제 tokenizer/토큰화/한영 평가 준비는 끝나지 않았다. 현재 I/O 대기를 고려해 작은 임시 입력과 GPU 계산 위주로 먼저 진행한다.

### 순서와 병행 관계

| 순서 | 별도 개발/검증 | 기존 서버 작업 | 선행 조건·다음으로 넘길 증거 | 개발 보조 모델 / effort |
|---|---|---|---|---|
| 0 | 상태·GPU·디스크·오류 이벤트 확인, 새 출력 경로 지정 | exact_v1·30초 감시 계속 | 점검 snapshot, 기존 작업과 겹치지 않는 경로 | 다음 작업의 모델 유지 |
| 1 / N1 | 후보 tokenizer 검사기 구현·CPU 검증 | 계속 | 다언어/특수토큰/Unicode fixture 결과, 검사 CLI·JSON 보고서 | Sol / High |
| 2 / N2 준비 | compile 검증 harness 옵션·시간 제한·결과 비교 보완 | 계속 | CPU/CLI 점검, 검증할 소스 해시·설정·입력 고정 | Astra / High |
| 3 / N2 실행 | 작은 8GPU compile=true suite 기동 | 계속 | GPU 비점유·잠금 확인, 독립 runs, 제한 시간·진행 이벤트 | Astra / High |
| 4 / N3 | N2가 실행되는 동안 평가 CLI·결과 저장을 CPU에서 개발 | 계속 + N2 GPU 실행 | 새 eval 파일·임시 checkpoint로 평가/결과 식별 검증 | Astra / High 유지 |
| 5 / N2 회수 | GPU suite 결과·소스 해시·연속/재개 수치 확인 | 계속 | summary/.done·오류 사례·비교 결과. 실패 시 N2 원인 해결 | Astra / High 유지 |
| 6 / N4 | 분리 정책·manifest 검증 및 토큰화 split 연결 | 계속 | 작은 입력에서 그룹 누출 거부·split 재현·토큰화 train/eval 배제 확인 | Sol / High |
| 7 / 실제 통합 | dedup 완료 검증·필요 범위 품질 검사·최종 입력 확정 | dedup 종료 후 | ready 조건과 N1/N4 적용 결과. 이후 실제 tokenizer로 진입 | Sol / High |

N1은 N2의 기술적 의존성은 없지만, 최종 tokenizer 단계의 막힌 검사를 먼저 해소하기 위해 앞에 둔다. N3도 N2 통과를 기다릴 필요는 없다. N2가 먼저 끝나면 결과를 먼저 회수하고 N3를 계속한다. N2가 실패해도 N1/N3의 독립 결과는 보존하며 관련 GPU 검증만 미완료로 남긴다. N4 코드 준비는 dedup 완료와 무관하게 가능하지만 실제 데이터 적용은 전체 품질 판단 이후다.

여기서 병행은 **서버의 데이터 프로세스 + 제한된 GPU 검증 + 대화 중 CPU 개발**이다. 단일 개발 세션으로 수행하며 서브에이전트 위임을 포함하지 않는다. 대화가 끝난 뒤 독립 개발이 자동으로 계속되는 것으로 설명하지 않는다.

### N1 — 후보 tokenizer 검사기

- 참고: REF-TOK T01/T02, REF-DATA D03. 대상: tokenizer/check_tokenizer.py, 대응 tests/test_tokenizer_check.py(신규 예정). 최종 학습기는 검사 연결이 필요한 부분만 후속 수정한다.
- tokenizer 경로·평가 입력·출력 경로를 CLI로 받는다. 누락 입력·비어 있는 범주를 성공으로 처리하지 않고, 외부 비교 tokenizer 다운로드는 기본 경로에서 분리한다.
- 한/영/코드/수학별 문서 수·문자/UTF8 byte·token 수·효율, vocab/ID 범위·필수 특수토큰/EOS·tokenizer SHA256을 보고한다. 효율 비율은 실제 값으로 제시하며 한국어 참고 목표만으로 전체 합격을 정하지 않는다.
- 복원 검사: NFC 기준 일반 텍스트, 개행/탭/들여쓰기/대소문자, 숫자/수식, 본문 속 특수토큰 literal. encode/decode 옵션을 명시해 특수토큰을 자동 생략한 결과를 원문 복원으로 간주하지 않는다.
- 코드 원문 보존과 tokenizer의 NFC는 별도 층이다. 조합형/분해형 Unicode가 포함된 코드 literal의 원문 차이를 따로 보고한다. 의미 보존 여부가 정해지지 않은 차이를 NFC 성공으로 숨기지 않으며, 필요 시 정규화 정책 결정을 실제 tokenizer 학습 전 남긴다.
- 완료 증거: 정상/실패 fixture·CLI 종료 코드·JSON 내용 검사, 변경 tokenizer에 과거 보고서 재사용 거부. 작은 시험 tokenizer 사용 가능. 실제 후보 학습·전체 자료 스캔은 수행하지 않는다.

### N2 — compile=true의 작은 8GPU 검증

- 참고: REF-TRAIN, REF-EVAL. 대상: scripts/validate_distributed.py. 기존 micro/vocab128/seq32/4 steps·2 step 저장/재개를 출발점으로 유지하고 compile 옵션과 사례별 timeout을 인자로 받을 수 있게 보완한다. 옵션은 아직 구현되지 않았다.
- 별도 runs/c2_compile_<새이름>에 설정·코드 해시·로그·checkpoint·summary를 저장한다. 검증 입력과 metadata는 compile=true로 고정하고 기존 compile=false checkpoint를 재개하지 않는다.
- 검사 범위: 동일 compiled 설정의 연속/재개 모델·optimizer·RNG·batch·LR/loss, holdout 1/7/9, 데이터 식별자 변경 거부, 한 랭크 NaN loss/gradient의 갱신 전 전역 종료. eager와 compiled 결과의 bitwise 일치를 합격 조건으로 혼동하지 않는다.
- 비교 기준은 실행 전 기록한다. 기존 연속/재개 tensor atol=1e-6/rtol=1e-5, loss 절대차 1e-6 및 batch/RNG 정확 일치를 최초 기준으로 사용한다. 실패하면 결과와 원인을 기록하고, 통과시키기 위해 사후에 조용히 허용치를 넓히지 않는다.
- 초기 컴파일을 고려한 예정 제한: 사례당 최대 900초, OMP/NUMEXPR/Inductor compile threads 각각 2. GPU 작업은 하나만 실행하고 benchmark 잠금을 공유한다. 초기 compile 시간과 학습 실행 시간을 나누어 보고하며 무응답은 timeout/error로 기록한다.
- 정상 완료 사례만 재사용하도록 config·소스 해시·결과 검증을 붙인다. 재사용 구현 전에는 기존 경로를 덮어쓰지 않고 새 실행 디렉터리를 사용한다. 모든 사례 통과 후에만 suite .done을 쓴다.
- 완료 증거: 사례별 종료 코드·실제 수치·checkpoint 비교·GPU 해제 확인·현재 소스 해시 일치. 작은 모델 통과를 1B/3B 처리량·실제 데이터 통과로 확대하지 않는다.

### N3 — 평가 실행 CLI와 보고서

- 참고: REF-EVAL E01~E03, REF-TOK T02. 대상: eval/run.py(신규 예정), eval/perplexity.py, eval/generation.py, 관련 tests. 기존 정확성 검증 함수를 재사용한다.
- 자체 checkpoint를 읽는 경로를 연결한다. DCP 로딩/필수 키·shape·모델 설정을 검증하고, 임시 micro checkpoint로 CPU에서 확인한다. HF export는 선행 조건으로 추가하지 않는다.
- 한/영 PPL 결과를 분리하고 전체 요약은 NLL 합/유효 표적 수로 계산한다. 고정 프롬프트 생성에는 원문/생성 token·EOS 종료 여부·seed·temperature·최대 길이를 저장한다. Base 프롬프트와 향후 Chat template를 구별한다.
- 보고서에 checkpoint 내용 식별자·tokenizer 해시·입력 snapshot·task/프롬프트 버전·코드/실행 설정·유효 표적 수를 기록한다. 실패/필수 입력 누락/0-target를 성공 보고서나 PPL=0으로 쓰지 않는다. 다른 입력으로 기존 보고서를 재사용하지 않는다.
- N2 실행 중에는 GPU를 사용하지 않고 신규 eval 파일·전용 tests만 수정한다. 학습 모델/데이터/공통 helper 수정이 필요하면 해당 변경을 N2 종료 뒤로 미룬다.
- 완료 증거: CPU micro end-to-end CLI, E01~E03 회귀, tokenizer/모델 불일치·불완전 checkpoint·잘못된 경로 거부, 재현 가능한 JSON 결과. 최종 tokenizer/실제 holdout이 없으므로 실제 한/영 점수는 미측정으로 남긴다.

### N4 — 그룹 분리·최종 manifest 검증

- 참고: REF-DATA D03, REF-TOK T02. 대상 후보: scripts/validate_source_manifest.py, data/split_manifest.py(신규 예정), common/data_contracts.py, data/tokenize_shards.py, 관련 tests. 문서: DATA_CONTRACT 갱신.
- 먼저 정책을 고정한다. 웹은 최종 중복 정책의 문서/그룹 식별자, 코드는 저장소 그룹을 포함해 분리한다. 코드가 여러 저장소에 중복되는 등 그룹이 연결되면 같은 분할에 배정하거나 제외하는 규칙을 명시한다. 부분/유사중복은 측정한 범위만 보장한다.
- 분리 manifest에는 source·최종 snapshot·파일/row·본문 식별자·group_id·split·seed·정책 버전을 기록한다. 현재 clean 파일에 없는 provenance를 있다고 가정하지 않는다. 실제 확보 가능한 필드와 재구성 필요 여부를 먼저 확인한다.
- 누락/중복 파일, 미완료 marker, 행 수/내용 식별자 불일치, 알 수 없는 split, train/eval의 문서·그룹 교집합을 거부한다. ready를 단순히 .done 개수로 승격하지 않는다.
- 현재 tokenize_shards.py는 마지막 샤드의 마지막 N문서를 holdout으로 쓴다. 그룹 분리를 적용하려면 명시적 split manifest 선택·캐시 식별을 토큰화에 연결해야 한다. 검사기만 만든 상태를 그룹 분리 구현 완료로 표시하지 않는다.
- 완료 증거: 작은 parquet의 동일 문서/저장소 교차 사례 거부, seed 고정 분리 재현, split 변경 시 캐시 거부, 실제 출력 token에서 평가 문서가 train에 섞이지 않음. 대량 분리·전수 중복 검사는 이후 실제 통합 단계로 남긴다.

### 실행 중 충돌 방지와 실제 통합 조건

- 기존 dedup 입력·정책·SQLite·manifest·출력을 유지한다. 신규 결과는 별도 경로에 기록하고 active DB에 코드를 추가하지 않는다.
- N2 준비가 끝나면 train/__init__.py·train/train.py·train/runtime.py·train/data.py·model/model.py와 검증 harness/입력/사용 helper를 suite 종료까지 고정한다. 현재 재개 계약이 코드 해시를 포함하므로 실행 중 변경은 재개 검증을 무효로 만든다. N3는 별도 eval 경로에서 진행한다.
- CPU 시험은 우선 OMP_NUM_THREADS=2와 작은 임시 입력으로 수행한다. GPU 검증 중 대량 hash/토큰화/전수 교차 스캔을 추가하지 않는다. 자원 문제가 생기면 새 검증 작업을 진단하고 기존 데이터 작업의 입력/설정을 임의 변경하지 않는다.
- dedup 완료 이벤트가 오면 신규 대량 작업보다 전체 summary·파일별 통계/읽기 검증을 먼저 한다. 추가 유사중복·코드 교차 범위·분리 정책·혼합 표본 판단을 실제 결과로 기록한다.
- 최종 진입 순서: 검증한 데이터 ready + N4 실제 분리 적용 → 후보 tokenizer 학습 → N1 실제 품질 확인/해시 고정 → split 기반 토큰화·holdout/mix → N3 실제 한/영 평가 + 실제 데이터/본학습 크기 분산 검증 → 리허설 준비. N2 합성 통과는 실제 데이터 검증을 대체하지 않는다.
- HF export·SFT·선호학습은 이번 별도 작업의 필수 범위에 넣지 않는다. 기존 본학습 시작·raw/clean/tokens 삭제/덮어쓰기·외부 업로드 확인 규칙을 유지한다.

### 단계 알림과 인계

| 시점 | 안내 |
|---|---|
| N1 시작 | `모델 전환 권장: Sol / High — tokenizer 검사·데이터 계약 보완` |
| N2 준비 시작 | `모델 전환 권장: Astra / High — compile·FSDP 재개와 오류 처리 검증` |
| N3 시작 | Astra / High 유지. 같은 모델 권장을 반복하지 않고 새 작업·검증 범위만 보고 |
| N4 시작 | `모델 전환 권장: Sol / High — 그룹 분리·manifest·토큰화 연결` |
| 복잡한 분산 오류가 재현된 경우 | 원인 분석 필요성을 설명하고 Astra / xhigh 권장. 일상 검증에는 High 유지 |

주 세션 자동 모델 변경/전환 완료 주장은 하지 않는다. 앱 선택기 또는 CLI /model 변경은 사용자가 수행한다. 권장 알림 자체 때문에 독립 작업을 멈추거나 반복 승인을 요구하지 않는다.

시작·오류·완료·timeout은 대화 중 보고 + 서버 이벤트 기록으로 남긴다. 기존 30초 감시는 로컬 프로세스로 유지하며 매회 모델을 호출하지 않는다. 신규 GPU 검증은 harness의 상태/timeout 이벤트를 기록하고, 기존 watcher에 통합할 필요가 있으면 기존 데이터 감시 동작을 보존해 검증한다. 외부 푸시나 대화 종료 후 자동 메시지를 약속하지 않는다.

각 N 작업 종료 시 STATUS에 `REF → 적용 파일 → 테스트 명령/로그 → 실제 결과 → 남은 실제 데이터/GPU 조건 → 다음 작업/모델`을 기록한다. N1~N4의 작은 입력 검증은 완료했으며, 다음은 실제 데이터 품질·분리 비율·tokenizer 정규화 정책 판단이다. 합성 검증 통과를 실제 데이터 ready로 간주하지 않는다.

## 이번 실행 결과

| 묶음 | 확인한 결과 | 남은 조건 |
|---|---|---|
| A | sampler v2 구현, D01/D02·입력 오류·결정성·v1 재현 CPU 통과. C2 작은 8GPU 재개도 통과 | 실제 데이터/본학습 크기 동등성 |
| B | 입력 schema·streaming·uint16·캐시·holdout·중단 복구·mix 구현, T01/T02 및 임시 parquet 검증 통과 | 최종 입력 ready 확정, 후보 tokenizer 품질 검사 경로 보완, 실제 토큰화/토큰량 검증 |
| C1 | native PPL/생성 평가 구현, E01~E03 및 작은 CPU Transformer 검증 통과 | 실제 한/영 holdout, N3 평가 실행 metadata/결과 저장 연결 |
| D | [데이터 계약](DATA_CONTRACT.md), 6소스 726파일 draft inventory 생성·draft 사용 거부 확인 | exact_v1 완료 검증, 유사중복·교차검사 범위·holdout 정책·표본 비율 확정 |
| C2 | 후속 실행에서 계약/RNG·전역 오류 거부·불균등 평가 보완, 작은 8GPU 합성 suite 통과 | 실제 한/영 holdout·본학습 크기·compile=true 재개 |
| E | 수행하지 않음 | HF export 필요 시 |

전체 CPU 테스트 39개 통과: `logs/tests_parallel_final.log`. 실제 tokenizer 학습·대량 토큰화·GPU 학습은 실행하지 않았다.

후속 C2 실행(2026-11-06): CPU 43개 통과(`logs/tests_c2_cpu.log`), 8GPU 합성 suite 6개 실행 사례 통과(`runs/c2_validation_v2/summary.json`). 연속/재개 텐서 84개 최대차 0, RNG·LR/loss·batch 일치, holdout 1/7/9 windows 기준값 일치, rank 3 NaN loss/gradient의 전역 갱신 전 종료 확인. 위 39개 문장은 최초 A/B/C1 실행 기록이다. 실제 최종 데이터 준비·모델 품질 검증은 아직 완료가 아니다.

## 출발점과 운영 원칙

### 03:21 UTC 재점검 — 다음 병행 착수 순서

exact_v1 528/662, 영어 36/56 진행. GPU 8장 각각 사용 메모리 0MiB, 계산 프로세스 없음(nvidia-smi utilization 값은 Not Found로 활용하지 않음). CPU pressure avg10=0, 호스트 I/O pressure full avg60≈39.82로 I/O 대기가 관측됨. 디스크 여유 974GiB, cgroup OOM 0. 따라서 대량 스캔 대신 아래의 작은 합성 검증을 우선한다. 이번 재점검은 읽기·계획 갱신이며 새 실행을 시작한 기록이 아니다.

1. **C2 코드 보완/CPU 검증 — 우선 착수 가능, Astra / High.** train/train.py의 재개 검사는 sampler 버전에 한정돼 있다. mix/tokenizer 및 재개에 영향을 주는 설정 식별자를 저장·비교하고, 비유한 loss/gradient를 optimizer 갱신 전에 모든 랭크에서 동일하게 처리한다. 평가의 rank별 forward 횟수도 맞추고 실제 표적만 합산한다.
2. **짧은 8GPU 합성 검증 — 위 보완 후 조건부 병행 가능, Astra / High.** 별도 tiny 설정·작은 vocab/seq·새 runs 디렉터리 사용. 연속 실행과 중간 저장/재개의 최종 설정·LR 일정은 동일하게 유지한다. holdout 1/7/9 windows처럼 world size에 맞지 않는 경우, 한 랭크 비유한 수치 주입, 미완료 checkpoint 제외, 입력 식별자 변경 거부를 제한 시간 안에 확인한다. 실행 직전 GPU 점유 재확인, 작은 checkpoint와 제한된 쓰기로 시작하며 실제 한/영 데이터 검증과 구분한다.
3. **후보 tokenizer 검사기 보완 — 즉시 개발 가능, Sol / High.** check_tokenizer.py의 고정 final/한국어 경로를 인자로 바꾸고 NFC·특수토큰 literal·한/영/수학/코드 효율과 복원 검사를 작은 fixture에 연결한다. 최종 tokenizer 학습은 ready 확정 후다.
4. **평가 결과 저장 및 D 품질 정책 준비 — 즉시 개발 가능.** 평가 결과에 checkpoint/tokenizer/prompt/seed 식별자와 언어 구분 기록(Astra / High). D는 유사중복 표본·코드 교차검사 범위·문서/저장소 분리 규칙 구체화(Sol / High). 전수 스캔은 현재 I/O 경쟁을 피하도록 이후 별도 실행한다.

최종 tokenizer·대량 토큰화·실제 데이터 학습은 기존 ready/검증 조건 대기. 완료한 A/B/C1과 GPU 성능 측정을 이유 없이 반복하지 않는다. 위 순서는 서버 데이터 작업과 독립 개발의 병행이며 서브에이전트 실행을 뜻하지 않는다.

- 기존 5개 소스의 exact_v1 중복 제거와 30초 상태 감시는 유지한다. 점검 시 snapshot은 519/662샤드 처리 중이며 전체 완료가 아니다.
- 6개 범주의 원본·정제본은 확보됐다. 코드 내부 전체 파일 정확중복 및 코드↔수학의 문서/fenced block 검사는 완료된 범위의 결과를 재사용한다. 동일 검사를 이유 없이 반복하지 않는다.
- 현재 기다릴 필요가 없는 작업은 sampler·토큰화 계약·평가 코드의 작은 합성 입력 기반 구현이다. 본학습 데이터 없이 CPU에서 먼저 검증한다.
- 여기서 병행은 기존 서버 작업을 실행하면서 독립 개발을 진행하는 뜻이다. 서브에이전트 위임이나 모델 자동 변경을 활성화하는 계획이 아니다.
- active `data/dedup.py`, exact_v1 SQLite·manifest·입력 순서·원본 정제 파일은 변경하지 않는다. 통합된 입력은 완료 후 새 manifest로 연결한다.
- 추가 개발 검증은 우선 CPU, `OMP_NUM_THREADS=2`, 작은 임시 fixture로 수행한다. 대량 데이터 재스캔·새 토큰화·GPU 성능 비교를 동시에 시작하지 않는다.

## 작업 묶음과 완료 조건

### A — sampler 경계·입력 검증 (우선순위 P0, 즉시 가능)

- 참고: REF-DATA D01,D02.
- 대상: `train/data.py`, 신규 `tests/test_sampler.py`.
- 작업: seq_len+1개인 단일 window 허용, 마지막 유효 시작점 포함, 짧은/빈/잘못된 byte 길이 샤드·비정상 가중치 검출. 실제 유효 window 수와 샤드 선택 기준을 명시한다.
- 재현성: (seed,step,rank,micro) 기반 stateless 방식 유지. 규칙 변경은 sampler 버전으로 기록한다. 기존 checkpoint의 버전이 불명확하면 같은 재개라고 조용히 처리하지 않는다.
- 완료 조건: D01의 입력/정답 일치, D02 마지막 시작점을 제어된 RNG나 직접 범위 검사로 확인. 잘못된 입력 거부, 같은 인자의 batch 재현 확인. 큰 데이터 읽기·GPU 불필요.
- checkpoint sampler 버전 metadata와 재개 guard는 이번에 연결했다. 실제 분산 동등성 및 tokenizer/mix 식별자는 C2에서 검증·보완한다. A와 C가 이 파일을 동시에 수정하지 않는다.
- 권장: **Sol / High**. 상태: 구현·CPU 검증 완료, 실제 분산 재개는 C2 미완료.

### B — 토큰화·캐시·데이터 입력 계약 (우선순위 P0, 즉시 가능)

- 참고: REF-TOK T01,T02, REF-DATA D03.
- 대상: `data/tokenize_shards.py`, `data/build_mix.py`, `tokenizer/train_tokenizer.py`, 필요 시 공통 manifest helper 및 대응 테스트.
- 먼저 정할 인터페이스: 소스별 입력 경로·처리 정책·revision·내용 버전, tokenizer JSON 내용 해시, EOS ID, dtype, seq_len, split 설정. 기존 raw/clean 출력 경로 상수를 최종 경로라고 가정하지 않는다.
- 작업: parquet batch 단위 토큰화, uint16 변환 전에 모든 ID 범위 검사, tokenizer/input/config가 바뀌면 이전 결과 재사용 거부. holdout 파일도 임시 파일→검증→완료 표시로 확정한다.
- mix 검사: 완료된 유효 shard만 포함, 빈 소스·잘못된 가중치·경로·실제 토큰 수 불일치 검출. 상대경로와 소스별 token 수/반복 노출량을 기록한다.
- 완료 조건: T01의 0/1/65535 허용 및 -1/65536 cast 이전 거부, T02의 같은 vocab 크기·다른 매핑에서 캐시 식별자 차이 확인. 작은 임시 parquet와 시험용 tokenizer로 중단/재개 및 split 배제 검사. 실제 최종 tokenizer 학습은 하지 않는다.
- 출력 스키마는 D와 공통으로 한 번 확정한다. 같은 helper를 두 작업에서 따로 구현하지 않는다.
- 권장: **Sol / High**. 상태: 구현·임시 입력 검증 완료, 실제 최종 데이터 검증 미완료.

### C — 최소 평가·학습 재개 계약 (우선순위 P1, 데이터와 독립)

- 참고: REF-EVAL E01,E02,E03, REF-TRAIN.
- 먼저 구현할 대상: 신규 `eval/perplexity.py`, 고정 생성 평가 인터페이스, 신규 테스트. 이 단계는 모델 품질 측정이 아니라 평가 함수의 정확성 검증이다.
- 합성 검증: context가 겹쳐도 각 다음 토큰 표적을 한 번만 평가, tail 포함, NLL sum/유효 토큰 수로 집계, 표적 0개는 명시적 오류. E01/E02/E03을 실제 평가 함수 테스트에 연결한다.
- 재현 자료: 한/영 별도 결과 형식, checkpoint·tokenizer·task/프롬프트 버전·seed·생성 길이·EOS 설정 기록.
- 후속으로 `train/train.py`에 연결: A의 sampler 버전 및 B의 tokenizer/mix 식별자를 checkpoint metadata에 기록하고 재개 시 확인. 비유한 gradient는 optimizer 갱신 전에 모든 랭크에서 일관되게 처리한다.
- 완료 조건을 둘로 나눈다. C1은 CPU 합성 입력의 평가 정확성; C2는 실제 holdout 및 8GPU 불균등 평가·연속/재개 비교다. C1 통과를 C2 완료로 표시하지 않는다.
- C2는 A/B 통과 및 작은 유효 token fixture 이후 별도 짧은 GPU 검증으로 실행한다. 대량 디스크 작업과 동시 실행 여부는 당시 I/O 상태로 결정한다.
- 권장: **Astra / High**, 복잡한 분산 오류 원인 분석 때만 xhigh. 상태: C1 CPU 및 C2 작은 8GPU 합성 검증 통과. 학습 중 평가에 계약 식별자/NLL/표적 수 기록 연결. 실제 한/영·고정 생성 결과 metadata·본학습 크기/compile 재개는 미완료.

### D — 최종 데이터 연결·품질 판단 준비 (우선순위 P1)

- 참고: REF-DATA D03, REF-TOK, 현재 코드/수학 교차검사 결과.
- 지금 가능한 작업: 샤드별 `.done` 집계로 소스별 문서 수·제거량 표 형식을 준비하고, 최종 source manifest와 tokenizer 표본 byte 비율의 초안을 작성한다. 비율 확정과 실제 token량 계산은 결과가 준비된 뒤 수행한다.
- 지금 가능한 설계: HPLT↔FW2 유사중복 표본·임계값·검사 예제 정의. URL 겹침률을 텍스트 삭제율로 사용하지 않는다. 코드↔한국어/영어 검사 범위와 평가 문항 오염 확인 방식을 명시한다.
- 완료 후 작업: exact_v1 `.done`·summary·샤드 통계 일치 확인, 필요 범위의 코드 교차검사·유사중복 측정 실행, 결과를 보고 추가 제거 또는 비중 조정 근거 기록.
- 최종 연결: 기존 5소스는 검증한 exact_v1 출력, 코드는 별도 검증한 코드 경로로 참조한다. 웹의 NFC dedup과 코드의 raw UTF8 정책 차이를 명시한다. 합친 원본을 복사하거나 active SQLite에 코드를 추가하지 않는다.
- holdout: 최종 중복 정책 적용 후 문서/중복 그룹을 나누고, 코드에서는 저장소 단위 분할 필요성도 검토한다. sample manifest의 파일 경로·source row·출처를 유지한다.
- 완료 조건: 최종 데이터 snapshot·소스 구성·품질 결정·분리 규칙이 고정되고 B의 입력 계약으로 읽힌다. 그 전에 최종 tokenizer나 대량 토큰화는 시작하지 않는다.
- 권장: **Sol / High**. 상태: schema·draft inventory 준비 완료; 실제 품질 판단·최종 ready 미완료.

### E — HF export 준비 (우선순위 P2, 조건부)

- 참고: REF-EXPORT X01.
- 자체 모델 평가 경로를 우선 사용하고 HF export가 필요할 때 구현한다. 현재 자체 학습/평가를 기다리게 만드는 공통 선행 조건으로 두지 않는다.
- 필요 시 tiny tied/untied 모델의 필수 키·shape 검사, 누락 Q/K/V 거부, 별도 LM head와 norm eps 보존, 변환 전후 logits 동등성부터 검증한다.
- 권장: **Astra / High**. 상태: 필요성에 따른 후속 작업, 미구현.

## 실행 순서와 파일 충돌 방지

| 순서 | 기존 서버 작업 | 독립 개발 | 다음으로 넘길 증거 |
|---|---|---|---|
| 1 | exact_v1 계속 실행 | A sampler 수정·합성 경계 테스트 | D01/D02 결과, sampler 버전 초안 |
| 2 | exact_v1 계속 실행 | B 입력 manifest·token ID·캐시·streaming 테스트 | T01/T02 결과, D와 공유할 schema |
| 3 | exact_v1 계속 실행 | C1 최소 평가 CPU 검증, D 품질/통합 설계 | E01~E03 결과, 품질 판단에 필요한 검사 목록 |
| 4 | exact_v1 완료 확인 | D의 실제 통계·유사중복/교차검사·입력 확정 | 최종 데이터 snapshot과 tokenizer 입력 |
| 5 | 대량 작업 I/O 상태 확인 | A/B 버전 연동·C2 짧은 8GPU 검증 | 실제 분산/재개 증거. tokenizer 준비와 독립 가능 |
| 6 | 최종 데이터·입력 계약 통과 | 최종 tokenizer → 토큰화·holdout/mix | tokenizer/토큰 데이터 검증 결과 |
| 7 | 데이터·평가·학습 검증 통과 | 1B 리허설 준비·실행 | 실제 learning/eval 곡선·재개 결과 |

- A의 `train/data.py`와 B의 token 생성은 seq_len/input dtype/유효 window 계약을 공유하므로 구현 순서를 A→B로 둔다.
- C1의 신규 eval 파일과 D의 보고서 설계는 A/B와 논리적으로 독립적이다. 단일 작업자가 단계적으로 진행하며 현재는 멀티에이전트 실행을 허용한 상태가 아니다.
- `train/train.py`와 `train/runtime.py`에 sampler·데이터/tokenizer/mix·설정·world/code 식별자 저장/비교 및 RNG 복원을 연결했고 작은 분산 검증을 통과했다. 최종 source manifest helper는 B가 정의하고 D가 사용한다.
- 기존 dedup 프로세스가 사용하는 파일·DB는 건드리지 않는다. 새로운 대량 교차 스캔은 필요성을 먼저 정하고, CPU 단위 테스트와 구분해 실행한다.
- 테스트 코드는 임시 경로를 사용한다. 실제 data/raw·clean·tokens 삭제/덮어쓰기는 기존 사용자 확인 규칙을 따른다.

## 보고와 모델 전환

작업 시작 때 `작업 A/B/C/D, REF 및 case, 권장 모델/effort`를 알린다. A/B/D는 Sol / High, C/E는 Astra / High 전환 권장을 안내한다. 주 대화의 모델 자동 변경이나 서브에이전트 위임은 수행하지 않는다.

각 작업 종료 시 STATUS에 `완료한 코드 → 실제 테스트 명령·로그 → 해당 case 결과 → 미해결 실제 데이터/GPU 검증 → 다음 작업`을 기록한다. 30초 서버 감시는 기존 데이터 작업의 상태 변화만 기록하며 아직 실행하지 않은 개발 작업을 완료로 표시하지 않는다.

최초 계획 작성 뒤 A/B/C1 및 D 초안, 후속 C2 작은 합성 분산 검증까지 수행했다. 다음 대상은 D의 남은 품질 판단 준비와 후보 tokenizer 검사기 보완이다. C2의 실제 데이터/본학습 크기/compile 검증은 별도로 남아 있다. 기존 중복 제거와 30초 감시는 계속 실행하며, 최종 tokenizer는 ready 조건 충족 후 시작한다.
