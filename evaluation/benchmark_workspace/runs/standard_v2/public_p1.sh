#!/bin/bash
# 1순위 공개 모델: 모델마다 dev 변형(dev_selection.yaml public_models) → test A(ko_mc_v1, ko_mc_ext_v1, ppl_v1, en_mc_v1).
# 실행 단위마다 별도 프로세스·로그(runs/standard_v2/public_p1/<model>__<suite>__<variant>.log). 실패해도 다음으로 진행, 완료 결과는 건너뜀.
cd /home/work/llm-dense/llm-test/ko-llm-bench-suite && source ../env/paths.sh && export LT_SRC
LOGD=../runs/standard_v2/public_p1
DENSE="llama-3.2-3b llama-3.2-3b-instruct qwen2.5-3b qwen2.5-3b-instruct kanana-1.5-2.1b-base kanana-1.5-2.1b kanana-2-3b-base kanana-2-3b qwen3-4b-base qwen3-4b-2507 smollm3-3b-base smollm3-3b exaone-3.5-2.4b"
MOE="qwen3-30b-a3b-base qwen3-30b-a3b-2507 kanana-2-30b-a3b-base kanana-2-30b-a3b nemotron-3-nano-30b-a3b-base nemotron-3-nano-30b-a3b"
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
