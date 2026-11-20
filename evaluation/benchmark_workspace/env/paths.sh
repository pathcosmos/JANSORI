# source env/paths.sh  — 모든 셸에서 먼저 읽는다. 값은 환경변수로 덮어쓸 수 있다.
export LT_PROJECT="${LT_PROJECT:-/home/work/llm-dense/llm-test}"      # NFS: 코드·스크립트·결과
export LT_LOCAL="${LT_LOCAL:-/workspace/llm-test}"                      # 로컬 overlay: venv·모델 캐시 (컨테이너 재생성 시 사라짐)
export LT_VENV="$LT_LOCAL/venv"
export LT_BIN="$LT_LOCAL/bin"
export HF_HOME="$LT_LOCAL/hf"                                           # 모델 다운로드 캐시
export UV_CACHE_DIR="$LT_LOCAL/cache/uv"
export XDG_CACHE_HOME="$LT_LOCAL/cache"
export VLLM_CACHE_ROOT="$LT_LOCAL/cache/vllm"
export OLLAMA_MODELS="$LT_LOCAL/ollama-models"
export OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
export LT_SRC="$LT_LOCAL/src"                                           # 외부 소스 체크아웃 (EVAFRILL-Mo 등)
# 기존 HF 로그인 토큰 파일 재사용 (HF_HOME 변경 시 기본 경로를 못 찾으므로 명시)
if [ -z "${HF_TOKEN:-}" ] && [ -f "$HOME/.cache/huggingface/token" ]; then
  export HF_TOKEN_PATH="$HOME/.cache/huggingface/token"
fi
export HF_HUB_DISABLE_TELEMETRY=1 VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1 OLLAMA_NO_CLOUD=true
export PATH="$LT_BIN:$LT_LOCAL/ollama/bin:$PATH"
