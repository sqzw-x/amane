#!/usr/bin/env bash
# Build a patched waifu2x-ncnn binary that can run process_cpu without a Vulkan ICD.
set -euo pipefail

DEST="${1:-/opt/amane/sr}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORK="${WORKDIR:-/tmp/sr-cpu-build}"
TAG="${WAIFU2X_TAG:-20250915}"

mkdir -p "$WORK" "$DEST/waifu2x"
cd "$WORK"

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
cmake --build waifu2x-ncnn-vulkan/build -j"$(nproc)"

install -m755 waifu2x-ncnn-vulkan/build/waifu2x-ncnn-vulkan "$DEST/waifu2x/waifu2x-ncnn-vulkan"
cp -a waifu2x-ncnn-vulkan/models/. "$DEST/waifu2x/"
