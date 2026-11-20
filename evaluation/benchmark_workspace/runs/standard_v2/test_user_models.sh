#!/bin/bash
# 사용자 모델 test 실행: A 표(공통 프로토콜) 전체 + B 표(best_settings에서 A와 다른 것만: JANSORI docstart=eos).
# PPL(ppl_v1)은 A에서 이미 문서 앞 EOS(prefix_token_id)로 계산하므로 B를 따로 돌리지 않는다.
# (모델, 스위트, 변형)마다 별도 프로세스. 완료된 태스크는 실행기가 건너뛴다.
cd /home/work/llm-dense/llm-test/ko-llm-bench-suite && source ../env/paths.sh && export LT_SRC
PLAN=()
for m in jansori-bf16 frankenstallm-bf16 evafrill-mo-3b evafrill-mo-3b-pretrain; do
  for s in ko_mc_v1 ko_mc_ext_v1 ppl_v1 en_mc_v1; do PLAN+=("$m|$s|A"); done
done
for s in ko_mc_v1 ko_mc_ext_v1 en_mc_v1; do PLAN+=("jansori-bf16|$s|docstart=eos"); done
for item in "${PLAN[@]}"; do
  IFS='|' read -r m s v <<< "$item"
  echo "=== $(date -Is) START $m $s $v"
  $LT_VENV/bin/python -m kobench.standard.run --models "$m" --suite "$s" --variant "$v"; rc=$?
  echo "=== $(date -Is) END $m $s $v exit=$rc"
done
echo "=== ALL DONE"
