#!/bin/bash
# 1순위 공개 모델 B 표: best_settings.yaml(dev 선택, 2026-11-20)에서 A와 다른 모델만 test 실행.
# smollm3-3b, llama-3.2-3b-instruct → chat=1. PPL은 chat 템플릿과 무관(문서 이어쓰기)이라 돌리지 않는다.
cd /home/work/llm-dense/llm-test/ko-llm-bench-suite && source ../env/paths.sh && export LT_SRC
LOGD=../runs/standard_v2/public_p1_b
one() {  # model suite variant
  local tag="$1__$2__${3//[=,]/_}"
  echo "=== $(date -Is) START $1 $2 $3"
  $LT_VENV/bin/python -m kobench.standard.run --models "$1" --suite "$2" --variant "$3" > "$LOGD/$tag.log" 2>&1; local rc=$?
  echo "=== $(date -Is) END $1 $2 $3 exit=$rc"
}
for m in smollm3-3b llama-3.2-3b-instruct; do
  for s in ko_mc_v1 ko_mc_ext_v1 en_mc_v1; do one "$m" "$s" chat=1; done
done
echo "=== ALL DONE"
