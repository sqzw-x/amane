#!/usr/bin/env python3
"""构建 Android 壳的 APK (Kotlin + Gradle), 供 `just android-app` 调用.

纯 Python 实现 (原为 build_android_app.sh): 不依赖 bash, Windows 上也能运行.

Env:
  AMANE_ANDROID_OUT   输出 APK 路径 (默认 dist/Amane-app-<version>.apk)
  AMANE_ANDROID_TASK  gradle 任务 (默认按 androidapp/keystore.properties 是否存在
                      选择 assembleRelease, 否则 assembleDebug)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "androidapp"
KEYSTORE = APP_DIR / "keystore.properties"
VERSION_FILE = APP_DIR / "version.txt"
LOCAL_PROPERTIES = APP_DIR / "local.properties"


def _launcher_name(platform: str) -> str:
    """Windows 使用批处理启动器, 其它平台使用 POSIX 脚本; 两者都在 androidapp/ 内."""
    return "gradlew.bat" if platform == "win32" else "gradlew"


def _read_version(path: Path) -> str:
    """读取 APP 版本; 与 Gradle 的 ``readText().trim()`` 一致."""
    return path.read_text(encoding="utf-8").strip()


def _select_task(explicit: str, keystore: Path) -> tuple[str, bool]:
    """返回 (gradle 任务, 是否退化为 debug 包); 显式指定的任务优先."""
    if explicit:
        return explicit, False
    if keystore.is_file():
        return "assembleRelease", False
    return "assembleDebug", True


def _apk_path(task: str) -> Path:
    """gradle 任务 → APK 路径; 只有 assembleRelease 产出 release 包."""
    kind = "release" if task == "assembleRelease" else "debug"
    return APP_DIR / "app" / "build" / "outputs" / "apk" / kind / f"app-{kind}.apk"


def _sdk_configured(env: Mapping[str, str], local_properties: Path) -> bool:
    """SDK 位置取自环境变量或 androidapp/local.properties; 两处都没有时 Gradle 无法配置."""
    return bool(env.get("ANDROID_SDK_ROOT") or env.get("ANDROID_HOME")) or local_properties.is_file()


def _output_path(explicit: str, version: str) -> Path:
    """输出路径; 相对路径相对仓库根解析, 与原脚本先 cd 到仓库根一致."""
    if not explicit:
        return ROOT / "dist" / f"Amane-app-{version}.apk"
    path = Path(explicit)
    return path if path.is_absolute() else ROOT / path


def _run_gradle(launcher: Path, task: str) -> int:
    """在 androidapp/ 内运行 Gradle, 返回退出码."""
    return subprocess.run([str(launcher), "--console=plain", task], cwd=APP_DIR, check=False).returncode


def main() -> int:
    launcher = APP_DIR / _launcher_name(sys.platform)
    if not launcher.is_file():
        sys.stderr.write(f"missing {launcher.name} (generate it once with: cd androidapp && gradle wrapper)\n")
        return 1

    if not _sdk_configured(os.environ, LOCAL_PROPERTIES):
        sys.stderr.write(
            "Android SDK not found: set ANDROID_SDK_ROOT/ANDROID_HOME or write androidapp/local.properties\n"
        )
        return 1

    version = _read_version(VERSION_FILE)
    if not version:
        sys.stderr.write("empty androidapp/version.txt\n")
        return 1

    task, debug_fallback = _select_task(os.environ.get("AMANE_ANDROID_TASK", ""), KEYSTORE)
    if debug_fallback:
        sys.stderr.write("androidapp/keystore.properties not found; building a debug-signed APK\n")

    code = _run_gradle(launcher, task)
    if code != 0:
        return code

    apk = _apk_path(task)
    if not apk.is_file():
        sys.stderr.write(f"APK missing: {apk}\n")
        return 1

    out = _output_path(os.environ.get("AMANE_ANDROID_OUT", ""), version)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(apk, out)
    print(f"APK={out}")  # noqa: T201
    print(f"{out.stat().st_size / (1024 * 1024):.1f} MB")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
