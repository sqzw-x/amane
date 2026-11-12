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
# 解释器由基础镜像提供, 禁止 uv 另下托管版本. .python-version 一并拷入, 让"镜像该给哪个
# 小版本"成为显式请求: 基础镜像被换成 3.15 时会立刻失败, 而不是静默换解释器后照常出包.
ENV UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock README.md .python-version ./
COPY src ./src
RUN uv sync --no-dev --frozen --no-editable

# --- 最终镜像 ---
FROM base
# libgomp1: 超分二进制的运行时依赖, Real-ESRGAN 与 waifu2x 都动态链接 libgomp.
# postgresql-client: r18.dev dump 导入走 psql -f 子进程 (见 docs/dev/crawlers.md). 不配 r18 时无害.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 postgresql-client \
    && rm -rf /var/lib/apt/lists/*
COPY alembic.ini ./
COPY --from=web-builder /app/web/dist ./web/dist

EXPOSE 8000
VOLUME ["/data", "/media"]

ENV AMANE_DATA_DIR=/data
ENV AMANE_LOG_DIR=/data/logs
ENV PATH="/app/.venv/bin:$PATH"
ENV UV_NO_SYNC=1

CMD ["python", "-m", "amane.server"]
