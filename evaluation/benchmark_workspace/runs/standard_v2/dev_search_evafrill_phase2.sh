#!/bin/bash
# EVAFRILL 2차: 선택된 체크포인트(pretrain, base)의 변형
cd /home/work/llm-dense/llm-test/ko-llm-bench-suite && source ../env/paths.sh && export LT_SRC
PLAN=(
 "evafrill-mo-3b-pretrain|docstart=eos" "evafrill-mo-3b-pretrain|shots=0"
)
PLAN_UNUSED=(
 "jansori-bf16|A" "jansori-bf16|docstart=eos" "jansori-bf16|shots=0" "jansori-bf16|docstart=eos,shots=0"
 "frankenstallm-bf16|A" "frankenstallm-bf16|docstart=eos" "frankenstallm-bf16|shots=0" "frankenstallm-bf16|chat=1" "frankenstallm-bf16|chat=1,shots=0"
 "evafrill-mo-3b-pretrain|A" "evafrill-mo-3b-sft-v2|A" "evafrill-mo-3b-dpo-r1|A" "evafrill-mo-3b-dpo-r2|A"
 "evafrill-mo-3b-dpo-r3|A" "evafrill-mo-3b-orpo|A" "evafrill-mo-3b|A"
)
for item in "${PLAN[@]}"; do
  m=${item%%|*}; v=${item#*|}
  echo "=== $(date -Is) START $m $v"
  $LT_VENV/bin/python -m kobench.standard.run --models "$m" --suite dev_mc_v1 --variant "$v"; rc=$?
  echo "=== $(date -Is) END $m $v exit=$rc"
done
echo "=== ALL DONE"
