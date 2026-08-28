#!/usr/bin/env bash
# Build a patched waifu2x-ncnn binary that can run process_cpu without a Vulkan ICD.
set -euo pipefail

DEST="${1:-/opt/amane/sr}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORK="${WORKDIR:-/tmp/sr-cpu-build}"
TAG="${WAIFU2X_TAG:-20250915}"
# Optional GitHub HTTPS prefix for regions where github.com is unreachable,
# e.g. GH_PROXY=https://gh-proxy.com  (rewrites submodule URLs too).
GH_PROXY="${GH_PROXY:-}"

jobs_default() {
  local kb
  kb="$(awk '/MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null || true)"
  # ncnn + glslang at -j2 OOMs ~4Gi boxes (SIGKILL 137).
  if [[ -n "${kb}" && "${kb}" -lt 5000000 ]]; then
    echo 1
  else
    nproc
  fi
}

JOBS="${JOBS:-$(jobs_default)}"

mkdir -p "$WORK" "$DEST/waifu2x"
cd "$WORK"

if [[ -n "$GH_PROXY" ]]; then
  cfg="$WORK/.gitconfig-mirror"
  printf '[url "%s/https://github.com/"]\n\tinsteadOf = https://github.com/\n' "${GH_PROXY%/}" >"$cfg"
  export GIT_CONFIG_GLOBAL="$cfg"
fi

if [[ ! -d waifu2x-ncnn-vulkan/.git ]]; then
  git clone --depth 1 --branch "$TAG" https://github.com/nihui/waifu2x-ncnn-vulkan.git
  git -C waifu2x-ncnn-vulkan submodule update --init --recursive --depth 1
fi

python3 "$SCRIPT_DIR/patch_cpu.py" "$WORK/waifu2x-ncnn-vulkan/src"

cmake -S waifu2x-ncnn-vulkan/src -B waifu2x-ncnn-vulkan/build \
  -DCMAKE_BUILD_TYPE=Release \
  -DNCNN_BUILD_TOOLS=OFF \
  -DNCNN_BUILD_EXAMPLES=OFF \
  -DNCNN_BUILD_BENCHMARK=OFF \
  -DNCNN_BUILD_TESTS=OFF
cmake --build waifu2x-ncnn-vulkan/build -j"$JOBS"

install -m755 waifu2x-ncnn-vulkan/build/waifu2x-ncnn-vulkan "$DEST/waifu2x/waifu2x-ncnn-vulkan"
cp -a waifu2x-ncnn-vulkan/models/. "$DEST/waifu2x/"
