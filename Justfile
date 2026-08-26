default:
    @just --list

CHECK_DIRS := "src/amane tests scripts"

host := env("AMANE_HOST", "127.0.0.1")
port := env("AMANE_PORT", "8000")

# Sync Python + web deps; pull test fixtures; install prek hooks
setup: sync fixtures
    -git config --unset-all core.hooksPath
    uv run prek install

# Local: full Python (extras+dev+packaging) + web
sync: _sync-python _web-install

_sync-python:
    uv sync --all-extras --dev --group packaging

[working-directory('web')]
_web-install:
    pnpm install

# 拉取爬虫测试 fixture (amane-testdata, 按 tests/.fixtures-rev)
# 无访问权限时打印警告并跳过, 爬虫用例会 skip 但不阻断 setup/check.
fixtures:
    uv run python scripts/fetch_fixtures.py

# CI check job: frozen Python(dev+extras) + frozen web
ci-sync: _ci-sync-python _web-install-ci

_ci-sync-python:
    uv sync --frozen --all-extras --dev

[working-directory('web')]
_web-install-ci:
    pnpm install --frozen-lockfile

lint:
    uv run ruff check {{ CHECK_DIRS }}
    uv run ruff format --check {{ CHECK_DIRS }}

fix: fix-python fix-web

# pre-commit: Python 自动修复 (ruff check --fix + format)
fix-python:
    uv run ruff check --fix {{ CHECK_DIRS }}
    uv run ruff format {{ CHECK_DIRS }}

# pre-commit: Web 自动修复 (oxfmt + oxlint --fix)
[working-directory('web')]
fix-web:
    pnpm fix

typecheck:
    uv run pyright
    uv run ty check

# web: oxlint + oxfmt + tsc + i18n
[working-directory('web')]
check-web:
    pnpm check

test: fixtures
    uv run pytest tests/ -q -n auto

# Test with coverage (CI artifact)
test-cov: fixtures
    uv run pytest tests/ -n auto --cov=amane --cov-report=term-missing --cov-report=html:coverage_html --cov-report=xml:coverage.xml

check: lint typecheck check-web test-cov

# Fail if OpenAPI / generated client drifted from source
_check-openapi: generate
    git diff --exit-code --stat web/openapi.json web/src/client

# Full CI gate after tool setup (uv/pnpm/node/just)
ci: ci-sync _check-openapi check build

deps: _deps-python _web-deps

_deps-python:
    uv tree -d 1 --outdated

[working-directory('web')]
_web-deps:
    pnpm deps

# Export OpenAPI + generate frontend client
generate: _openapi _web-gen-client

_openapi:
    uv run python scripts/export_openapi.py

[working-directory('web')]
_web-gen-client:
    pnpm gen-client

alias api := generate

dev: generate _dev-servers

[parallel]
_dev-servers: _dev-uvicorn _dev-web

_dev-uvicorn:
    uv run uvicorn amane.api.app:create_app --factory --reload --host {{ host }} --port {{ port }}

[working-directory('web')]
_dev-web:
    pnpm dev

build: generate _web-build

[working-directory('web')]
_web-build:
    pnpm build

[env('AMANE_HOST', host)]
[env('AMANE_PORT', port)]
start: build
    uv run python -m amane.server

# Preview docs site (http://127.0.0.1:8001)
docs:
    uv run --group docs mkdocs serve --dev-addr {{ host }}:{{ port }}

# Rasterize assets/logo.svg → favicon + app.ico + app.icns
icons:
    uv run python scripts/generate_icons.py

# macOS-only: sync → web → frontend build → PyInstaller .app
macos-app: sync build
    bash scripts/build_macos_app.sh

# Windows-only: sync → web → frontend build → PyInstaller onedir + Native AOT shell
windows-app: sync build
    pwsh -NoProfile -File scripts/build_windows_app.ps1

# Run the menu bar UI standalone against a running dev server
bar-run:
    cd macapp && swift run AmaneUI --base-url http://{{ host }}:{{ port }}

# Tray-only against a running dev server (Windows; no supervisor)
[env('AMANE_UI_ONLY', '1')]
[env('AMANE_HOST', host)]
[env('AMANE_PORT', port)]
windows-bar:
    dotnet run --project winapp

# Full local gate: generate → fix → check → build
all: generate fix check build

# Bump version, generate client, commit, tag vX.Y.Z (does not push)
bump kind:
    uv run python scripts/bump_version.py precheck {{ kind }}
    uv version --bump {{ kind }}
    just generate
    uv run python scripts/bump_version.py commit

# Preview next version / commit / tag (no writes)
bump-dry kind:
    uv run python scripts/bump_version.py precheck {{ kind }} --dry-run

# Strip magic trailing commas (imports + ≤3-arg defs/calls), then ruff format
strip-commas *args:
    uv run python scripts/strip_magic_commas.py {{ args }}

# Replay a scrape task from a task record (dir or zip)
repro path *args:
    uv run python -m amane.observability {{ path }} {{ args }}
