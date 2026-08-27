#!/usr/bin/env python3
"""按 tests/.fixtures-rev 检出 amane-testdata → tests/crawlers/cases/ (gitignored).

纯 Python 实现 (原为 fetch_fixtures.sh): 便于在 Linux / macOS / Windows 三平台跑 `just fixtures`,
不依赖 Windows 上不一定可用的 bash.

无权限时警告并成功退出, 爬虫 TOML 用例 skip、不阻断 just setup/check.

Env:
  AMANE_FIXTURES_URL     覆盖仓库 URL
  FIXTURES_DEPLOY_KEY    CI deploy key (PEM); 有则走 SSH
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPO = "https://github.com/sqzw-x/amane-testdata.git"
SSH_REPO = "git@github.com:sqzw-x/amane-testdata.git"
TARGET = ROOT / "tests" / "crawlers" / "cases"


def _read_rev() -> str:
    """读取 tests/.fixtures-rev 并去掉全部空白 (等价原 bash ``tr -d '[:space:]'``)."""
    return "".join((ROOT / "tests" / ".fixtures-rev").read_text().split())


# pre-commit / `git commit` 会注入 GIT_DIR 等, 子进程里的 `git -C cases` 会误操作主仓库 index.
_GIT_HOOK_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


def _run(cmd: list[str], env: dict[str, str]) -> bool:
    """在仓库根执行 git 命令, 返回是否成功."""
    clean = {k: v for k, v in env.items() if k not in _GIT_HOOK_VARS}
    return subprocess.run(cmd, cwd=ROOT, env=clean, check=False).returncode == 0


def main() -> int:
    rev = _read_rev()
    repo = os.environ.get("AMANE_FIXTURES_URL", DEFAULT_REPO)
    env = os.environ.copy()
    key_path: Path | None = None

    deploy_key = os.environ.get("FIXTURES_DEPLOY_KEY", "")
    if deploy_key:
        fd, raw = tempfile.mkstemp(suffix=".key")
        with os.fdopen(fd, "w") as fh:
            fh.write(deploy_key)
        key_path = Path(raw)
        # OpenSSH/CRT 要求私钥文件权限收紧; POSIX 上 chmod 生效, Windows 上尽力而为
        with contextlib.suppress(OSError):
            key_path.chmod(0o600)
        env["GIT_SSH_COMMAND"] = f"ssh -i {key_path} -o StrictHostKeyChecking=accept-new"
        repo = os.environ.get("AMANE_FIXTURES_URL", SSH_REPO)

    try:
        if (TARGET / ".git").exists():
            ok = _run(["git", "-C", str(TARGET), "fetch", "--quiet", "origin", rev], env)
            if ok:
                ok = _run(["git", "-C", str(TARGET), "checkout", "--quiet", "--force", rev], env)
        else:
            shutil.rmtree(TARGET, ignore_errors=True)
            ok = _run(["git", "clone", "--quiet", repo, str(TARGET)], env)
            if ok:
                ok = _run(["git", "-C", str(TARGET), "checkout", "--quiet", "--force", rev], env)
    finally:
        if key_path is not None:
            with contextlib.suppress(OSError):
                key_path.unlink()

    if not ok:
        sys.stderr.write("warning: could not fetch fixtures, crawler cases will be skipped\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
