# syntax=docker/dockerfile:1
# Amane 元数据管理服务

# --- 前端构建阶段 ---
FROM node:26-slim AS web-builder
WORKDIR /app/web
RUN npm install -g pnpm@11
# pnpm-workspace.yaml 含 allowBuilds; 必须在 install 前拷入, 否则 ERR_PNPM_IGNORED_BUILDS
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ .
RUN pnpm build

# --- Python 依赖阶段 ---
FROM python:3.14-slim AS base
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --no-dev --frozen --no-editable

# --- 最终镜像 ---
FROM debian:13-slim AS sr-bin
# patched waifu2x: 有 ICD 走 GPU, -g -1 走 process_cpu 且不 init Vulkan.
# 官方 Release zip 无 ICD 时 exit 127 / SIGSEGV; 见 docker/sr-cpu/.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates cmake g++ git python3 \
        libgomp1 libvulkan-dev glslang-tools pkg-config make \
    && rm -rf /var/lib/apt/lists/*
COPY docker/sr-cpu /src/sr-cpu
RUN bash /src/sr-cpu/build.sh /opt/amane/sr

FROM base
# libgomp1: ncnn OpenMP. libvulkan1: 捆绑二进制动态链接 loader (CPU 路径不需要 ICD).
# postgresql-client: r18.dev dump 导入走 psql -f 子进程 (见 docs/dev/crawlers.md). 不配 r18 时无害.
RUN apt-get update && apt-get install -y --no-install-recommends \
        postgresql-client libgomp1 libvulkan1 \
    && rm -rf /var/lib/apt/lists/*
COPY alembic.ini ./
COPY --from=web-builder /app/web/dist ./web/dist
COPY --from=sr-bin /opt/amane/sr /opt/amane/sr

EXPOSE 8000
VOLUME ["/data", "/media"]

ENV AMANE_DATA_DIR=/data
ENV AMANE_LOG_DIR=/data/logs
ENV AMANE_SR_BUNDLE_DIR=/opt/amane/sr
ENV PATH="/app/.venv/bin:$PATH"
ENV UV_NO_SYNC=1

CMD ["python", "-m", "amane.server"]
