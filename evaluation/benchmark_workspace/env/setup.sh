#!/usr/bin/env bash
# 실행 환경 구축 (재실행 가능: 완료 단계는 .done 마커로 건너뜀)
#   bash env/setup.sh            # 전체
#   bash env/setup.sh vllm       # 특정 단계만
# 버전 정책: 공개 2주 이상 지난 릴리스만 사용 → uv --exclude-newer 로 전이 의존성까지 고정.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
source "$here/paths.sh"

EXCLUDE_NEWER="2026-11-04T00:00:00Z"
UV_VERSION="0.13.7"
VLLM_VERSION="0.33.0"
LM_EVAL_VERSION="0.4.13"
OLLAMA_VERSION="v0.40.15"
EVAFRILL_REPO="https://github.com/pathcosmos/EVAFRILL-Mo"

log(){ printf '[setup %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
mkdir -p "$LT_LOCAL" "$LT_BIN" "$HF_HOME" "$LT_SRC" "$LT_LOCAL/markers" "$here/logs"
done_(){ [ -f "$LT_LOCAL/markers/$1.done" ]; }
mark(){ date -Is > "$LT_LOCAL/markers/$1.done"; }

step_uv(){
  done_ uv && return
  log "uv $UV_VERSION"
  curl -fsSL "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-unknown-linux-gnu.tar.gz" \
    | tar xz -C "$LT_BIN" --strip-components=1
  "$LT_BIN/uv" --version; mark uv
}

step_vllm(){
  done_ vllm && return
  log "venv + vllm $VLLM_VERSION + lm-eval $LM_EVAL_VERSION (시스템 NGC torch와 분리)"
  [ -d "$LT_VENV" ] || uv venv --python /usr/bin/python3.12 "$LT_VENV"
  VIRTUAL_ENV="$LT_VENV" uv pip install --exclude-newer "$EXCLUDE_NEWER" \
    "vllm==$VLLM_VERSION" "lm-eval[api,hf]==$LM_EVAL_VERSION" \
    hf_transfer sentencepiece tiktoken protobuf pyyaml requests numpy scipy matplotlib pytest
  mark vllm
}

step_mamba(){
  done_ mamba && return
  # mamba-ssm/causal-conv1d는 설치하지 않는다 (2026-11-18 확인):
  #  - venv torch 2.14+cu130용 prebuilt wheel 없음, 시스템 nvcc 12.8이라 소스 빌드 불일치
  #  - MAMBA_SKIP_CUDA_BUILD 설치는 패키지 import 시 selective_scan_cuda 를 요구해 실패하고,
  #    vLLM 의존성(tilelang, apache-tvm-ffi)을 하위 버전으로 바꾼다
  # 대신 vLLM 내장 Triton 커널을 쓴다: vllm.model_executor.layers.mamba.ops.{ssd_combined:mamba_chunk_scan_combined_varlen, causal_conv1d:causal_conv1d_fn/update, ssu_dispatch:selective_state_update}
  log "Mamba-2 커널: vLLM 내장 Triton ops 확인"
  "$LT_VENV/bin/python" -c "from vllm.model_executor.layers.mamba.ops.ssd_combined import mamba_chunk_scan_combined_varlen; from vllm.model_executor.layers.mamba.ops.ssu_dispatch import selective_state_update; from vllm.model_executor.layers.mamba.ops.causal_conv1d import causal_conv1d_fn, causal_conv1d_update; print('vllm mamba ops OK')"
  mark mamba
}

step_ollama(){
  done_ ollama && return
  log "ollama $OLLAMA_VERSION (로컬 설치, sudo 불필요)"
  mkdir -p "$LT_LOCAL/ollama" "$LT_LOCAL/downloads"
  local f="$LT_LOCAL/downloads/ollama-$OLLAMA_VERSION-linux-amd64.tar.zst"
  curl -fL --retry 3 -C - -o "$f" "https://github.com/ollama/ollama/releases/download/$OLLAMA_VERSION/ollama-linux-amd64.tar.zst"
  # 시스템에 zstd가 없으므로 Python zstandard로 스트리밍 해제
  uvx --exclude-newer "$EXCLUDE_NEWER" --from zstandard python -c \
    'import sys,zstandard;zstandard.ZstdDecompressor().copy_stream(open(sys.argv[1],"rb"),sys.stdout.buffer)' "$f" \
    | tar -x -C "$LT_LOCAL/ollama"
  "$LT_LOCAL/ollama/bin/ollama" --version || true; mark ollama
}

step_evafrill(){
  done_ evafrill && return
  log "EVAFRILL-Mo 소스"
  [ -d "$LT_SRC/EVAFRILL-Mo/.git" ] || git clone -q "$EVAFRILL_REPO" "$LT_SRC/EVAFRILL-Mo"
  git -C "$LT_SRC/EVAFRILL-Mo" rev-parse HEAD > "$here/evafrill-commit.txt"
  mark evafrill
}

LLAMACPP_TAG="b12084"          # 2026-11-03
MAMBA_SSM_VERSION="2.3.2.post1"
CAUSAL_CONV1D_VERSION="1.7.0"
BUILD_JOBS="${BUILD_JOBS:-6}"

# 빌드 전용 CUDA 13.0 툴킷 (venv와 분리). venv에 딸려 온 nvcc 13.4는 runtime/cccl 헤더 13.0과 섞여
# "CUDA compiler and CUDA toolkit headers are incompatible" 오류가 났다 → cuda-toolkit 메타패키지로 13.0 일관 고정.
CUDA_TK_VERSION="13.0.3.0"
cuda13_home(){
  local tk="$LT_LOCAL/cuda13-tk" home="$LT_LOCAL/cuda13-tk/nvidia/cu13"
  if [ ! -x "$home/bin/nvcc" ]; then
    uv pip install --exclude-newer "$EXCLUDE_NEWER" --target "$tk" "cuda-toolkit[all]==$CUDA_TK_VERSION" >&2
  fi
  [ -e "$home/lib64" ] || ln -sfn lib "$home/lib64"
  for so in "$home"/lib/*.so.*; do b="${so%%.so.*}.so"; [ -e "$b" ] || ln -sf "$(basename "$so")" "$b"; done
  echo "$home"
}

# sdist의 setup.py가 sm_75~sm_110을 하드코딩한다 → H200(sm_90)만 남겨 빌드 시간 단축
patched_sdist(){  # $1=pypi 이름 $2=버전 → 패치된 소스 디렉터리 출력
  local d="$LT_LOCAL/build/$1-$2"
  if [ ! -f "$d/setup.py" ]; then
    mkdir -p "$LT_LOCAL/build/dl" "$d"
    # uv venv에는 pip가 없다 → PyPI JSON에서 sdist URL·sha256을 받아 직접 내려받고 검증
    local url sha f
    read -r url sha < <(curl -fsSL "https://pypi.org/pypi/$1/$2/json" | "$LT_VENV/bin/python" -c \
      'import json,sys;u=[x for x in json.load(sys.stdin)["urls"] if x["packagetype"]=="sdist"][0];print(u["url"],u["digests"]["sha256"])')
    f="$LT_LOCAL/build/dl/$(basename "$url")"
    curl -fsSL -o "$f" "$url"
    echo "$sha  $f" | sha256sum -c - >&2
    tar xzf "$f" -C "$d" --strip-components=1
    "$LT_VENV/bin/python" - "$d/setup.py" >&2 <<'PY'
import re,sys
p=sys.argv[1]; s=open(p).read()
# 하드코딩된 gencode 목록 구성이 끝난 뒤(사용 직전)에 sm_90 하나로 덮어쓴다.
# (줄 삭제 방식은 if 블록 본문을 비워 IndentationError를 냈다)
anchor = "\n    if FORCE_CXX11_ABI:"
assert s.count(anchor) == 1, f'anchor count {s.count(anchor)}'
s2 = s.replace(anchor, '\n    cc_flag = ["-gencode", "arch=compute_90,code=sm_90"]  # kobench: H200(sm_90) 전용' + anchor)
# torch 2.14 헤더는 C++20을 요구한다 ("C++20 or later compatible compiler is required to use ATen")
n17 = s2.count('"-std=c++17"')
assert n17 >= 1, 'std flag not found'
s2 = s2.replace('"-std=c++17"', '"-std=c++20"')
compile(s2, p, "exec")
open(p,'w').write(s2); print('patched', p)
PY
  fi
  echo "$d"
}

step_cudaext(){
  done_ cudaext && return
  log "CUDA 확장 소스 빌드: causal-conv1d $CAUSAL_CONV1D_VERSION, mamba-ssm $MAMBA_SSM_VERSION (sm_90 전용, CUDA $CUDA_TK_VERSION)"
  local ch c m; ch="$(cuda13_home)"
  c="$(patched_sdist causal-conv1d "$CAUSAL_CONV1D_VERSION")"; m="$(patched_sdist mamba-ssm "$MAMBA_SSM_VERSION")"
  "$ch/bin/nvcc" --version | tail -2
  CUDA_HOME="$ch" PATH="$ch/bin:$PATH" CPATH="$ch/include:${CPATH:-}" LIBRARY_PATH="$ch/lib:${LIBRARY_PATH:-}" \
  TMPDIR="$LT_LOCAL/build/tmp" TORCH_CUDA_ARCH_LIST="9.0" MAX_JOBS="$BUILD_JOBS" NVCC_THREADS=2 \
  CAUSAL_CONV1D_FORCE_BUILD=TRUE MAMBA_FORCE_BUILD=TRUE \
  VIRTUAL_ENV="$LT_VENV" uv pip install --no-build-isolation --no-deps "$c" "$m"
  "$LT_VENV/bin/python" -c "import torch, causal_conv1d_cuda, selective_scan_cuda; from mamba_ssm.ops.triton.ssd_combined import mamba_chunk_scan_combined; from causal_conv1d import causal_conv1d_fn; print('cuda ext OK')"
  mark cudaext
}

step_llamacpp(){
  done_ llamacpp && return
  log "llama.cpp $LLAMACPP_TAG CUDA 빌드 (시스템 CUDA 12.8, sm_90)"
  local s="$LT_SRC/llama.cpp"
  [ -d "$s/.git" ] || git clone -q --depth 1 --branch "$LLAMACPP_TAG" https://github.com/ggml-org/llama.cpp "$s"
  cmake -S "$s" -B "$s/build" -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=90 -DCMAKE_BUILD_TYPE=Release \
    -DLLAMA_CURL=OFF -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF \
    -DCMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc
  # ↑ 웹 UI 미포함: 얕은 클론이라 버전이 b1로 해석되어 HF의 'latest' UI 에셋을 받고, 고정 태그와 파일 구성이 달라 실패했다.
  cmake --build "$s/build" -j "$BUILD_JOBS" --target llama-server llama-perplexity llama-cli llama-bench llama-quantize
  for b in llama-server llama-perplexity llama-cli llama-bench llama-quantize; do ln -sf "$s/build/bin/$b" "$LT_BIN/$b"; done
  "$LT_BIN/llama-server" --version 2>&1 | tail -2
  mark llamacpp
}

step_evaldeps(){
  done_ evaldeps && return
  # 생성 벤치 채점 의존성 (Goal 2): IFEval/IFEval-Ko 채점기(langdetect, immutabledict). 기존 패키지는 바꾸지 않음(dry-run 확인)
  log "lm-eval[ifeval] 채점 의존성"
  VIRTUAL_ENV="$LT_VENV" uv pip install --exclude-newer "$EXCLUDE_NEWER" "lm-eval[ifeval]==$LM_EVAL_VERSION"
  "$LT_VENV/bin/python" -c "import nltk; nltk.download('punkt_tab', quiet=True)"   # IFEval 문장 분절 데이터
  mark evaldeps
}

step_freeze(){
  log "버전 기록 → env/lock.txt"
  VIRTUAL_ENV="$LT_VENV" uv pip freeze > "$here/lock.txt"
}

steps=("$@"); [ ${#steps[@]} -eq 0 ] && steps=(uv vllm mamba ollama evafrill cudaext llamacpp evaldeps freeze)
for s in "${steps[@]}"; do "step_$s"; done
log "완료: ${steps[*]}"
