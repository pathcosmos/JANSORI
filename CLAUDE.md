# 프로젝트: 한국어 중심 Dense LLM (3B, MoE 아님) 직접 구현·학습

사용자는 한국어 화자다. **사용자에게는 한국어로 답한다.** 목적은 "직접 작성하고 수행하며 결과를 확인"하는 것이다
(프레임워크 의존 최소화: 순수 PyTorch + FSDP2). 품질 경쟁이 아니라 파이프라인 전체를 직접 만들어 돌려보는 데 의미가 있다.

## 새 기기에서 시작하는 순서
`python scripts/preflight.py` 실행 → 없는 항목 확인 → `docs/SETUP_CHECKLIST.md`를 위에서부터 수행 (데이터는 git에 없으므로 재다운로드/재생성 절차 포함). 완료할 때마다 `docs/STATUS.md` 갱신.

## 시작할 때 반드시 읽을 것 (순서대로)
0. `docs/SETUP_CHECKLIST.md` — 새 기기 세팅·데이터 재확보 체크리스트
1. `docs/STATUS.md` — 현재 어디까지 됐는지, **다음에 할 일** (가장 중요, 항상 최신으로 유지)
2. `docs/PLAN.md` — 확정된 전체 계획 (모델/데이터/학습 설정/일정)
3. `docs/DECISIONS.md` — 결정과 근거 (다시 논쟁하지 말 것)
4. `docs/LESSONS.md` — 이미 겪은 문제 (OOM 등)
5. `docs/RUNBOOK.md` — 단계별 실행 명령어

새 모델의 데이터·토크나이저·학습·평가·변환·SFT를 구현할 때는 `docs/FRANKENSTALLM_HANDOFF.md`에서 해당 단계의 REF 항목과 검증 입력을 참고한다. 적용 파일·테스트·남은 항목은 STATUS에 기록한다. FRANKENSTALLM 원본은 필요할 때 관련 파일만 읽으며, 과거 모델 복구는 새 모델 개발의 선행 조건이 아니다.

## 작업 규칙
- 사용자 최신 비용 지시(2026-11-07): **업로드·다운로드 비용 무료/최소화 → 저장 용량 비용 → 속도** 순으로 외부 저장·전송안을 선택한다. 전송 관련 API/조회/중계 요금도 포함한다. 무결성·재개·보존 조건은 필수다. 현재 서버는 추가 비용0원이며, 추가 비용 없는 CPU/RAM/GPU 최적화는 계속한다. 대표 입력에서 cache/worker/batch를 실측하고 작은 fixture 상한을 전체 데이터 기본값으로 승계하지 않는다.
- 각 단계 실행 중 다음 단계의 최적화 후보를 미리 확인하고, 자원 여유 안에서 독립 입력으로 선행 측정한다. 검증·재개·출력 동일성을 지키면서 총시간을 줄인 설정을 다음 실행 계약에 반영한다. 기존 측정은 재사용하고 추가 측정 비용을 제한한다. 상세 기준은 AGENTS.md·HARDWARE_RESOURCES를 따른다.
- 사용자 요청에 따라 향후 자원 집약 작업은 `docs/HARDWARE_RESOURCES.md`의 기기 실측·단계별 worker/thread·로컬/NFS 분리·병행 예산을 적용한다. `scripts/run_with_resources.py` 또는 동등한 명시적 설정을 사용하고 실제 적용값을 기록한다.
- 작업이 끝날 때마다 `docs/STATUS.md`를 갱신한다 (완료 수치, 다음 할 일).
- 백그라운드 작업은 **짧은 간격으로 확인**하고, 로그가 멈추면 즉시 원인(OOM: `/sys/fs/cgroup/memory.events`의 oom_kill)을 본다.
- 병렬 워커 수는 `cgroup memory.max`와 샤드당 메모리(정제 시 워커당 ~10-25GB)를 보고 정한다.
- 결과를 사실대로 보고한다. 실패/미검증 항목은 숨기지 않는다.
- **사용자 확인 후에만**: 본학습 시작, `data/raw|clean|tokens` 삭제/덮어쓰기, 외부 업로드.
- 경로는 `common/paths.py`(PROJECT_ROOT, 환경변수로 덮어쓰기 가능)만 쓴다. 절대경로 하드코딩 금지. 모든 명령은 프로젝트 루트에서 실행.
- 각 스크립트는 재시작 가능(.done 마커)하게 만든다.

## 구조
```
common/paths.py   data/{download,filters,clean,tokenize_shards,build_mix}.py   tokenizer/{train,check}_tokenizer.py
model/model.py    train/{train,data}.py + train/configs/   scripts/{setup_env.sh,check_env.py,make_manifest.py}
eval/ (CPU PPL·생성 구현, 실제/분산 검증 대기)   sft/ (미구현)   docs/   runs/ (학습 산출물)   logs/
```
