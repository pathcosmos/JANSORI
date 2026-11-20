#!/bin/bash
# 1순위 공개 모델 재시도(kanana-1.5, relax_head_dim_check): 모델마다 dev 변형(dev_selection.yaml public_models) → test A(ko_mc_v1, ko_mc_ext_v1, ppl_v1, en_mc_v1).
# 실행 단위마다 별도 프로세스·로그(runs/standard_v2/public_p1/<model>__<suite>__<variant>.log). 실패해도 다음으로 진행, 완료 결과는 건너뜀.
cd /home/work/llm-dense/llm-test/ko-llm-bench-suite && source ../env/paths.sh && export LT_SRC
LOGD=../runs/standard_v2/public_p1_retry1
DENSE="kanana-1.5-2.1b-base kanana-1.5-2.1b"   # 재시도: 1차는 transformers 5.20 head_dim 검증으로 로드 실패(public_p1/ 로그 보존)
MOE=""
one() {  # model suite variant
  local tag="$1__$2__${3//[=,]/_}"
  echo "=== $(date -Is) START $1 $2 $3"
  $LT_VENV/bin/python -m kobench.standard.run --models "$1" --suite "$2" --variant "$3" > "$LOGD/$tag.log" 2>&1; local rc=$?
  echo "=== $(date -Is) END $1 $2 $3 exit=$rc"
}
for m in $DENSE $MOE; do
  kind=$($LT_VENV/bin/python -c "from kobench.standard.registry import load_models;print(load_models()['$m'].kind)")
  if [ "$kind" = base ]; then VS="A shots=0"; else VS="A chat=1 shots=0"; fi
  for v in $VS; do one "$m" dev_mc_v1 "$v"; done
  for s in ko_mc_v1 ko_mc_ext_v1 ppl_v1 en_mc_v1; do one "$m" "$s" A; done
done
echo "=== ALL DONE"
