#!/usr/bin/env bash
# Android-only: build the WebView shell APK (Kotlin + Gradle).
# Not for macOS/Windows packaging. Expects: JDK 17+ and an Android SDK
# (ANDROID_SDK_ROOT / ANDROID_HOME, or androidapp/local.properties).
# Prefer: just android-app
#
# Env:
#   AMANE_ANDROID_OUT   output APK path (default: dist/Amane-<version>-android.apk)
#   AMANE_ANDROID_TASK  gradle task (default: assembleRelease when
#                       androidapp/keystore.properties exists, otherwise assembleDebug)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP_DIR="$ROOT/androidapp"
KEYSTORE="$APP_DIR/keystore.properties"

if [[ ! -x "$APP_DIR/gradlew" ]]; then
  echo "missing androidapp/gradlew (generate it once with: cd androidapp && gradle wrapper)" >&2
  exit 1
fi

if [[ -z "${ANDROID_SDK_ROOT:-}${ANDROID_HOME:-}" && ! -f "$APP_DIR/local.properties" ]]; then
  echo "Android SDK not found: set ANDROID_SDK_ROOT/ANDROID_HOME or write androidapp/local.properties" >&2
  exit 1
fi

VERSION="$(uv run python -c 'from amane.version import get_version; print(get_version())')"
# versionCode 必须随版本单调递增, 否则 Android 拒绝覆盖安装; semver 三段各占两位十进制.
VERSION_CODE="$(python3 - "$VERSION" <<'PY'
import sys

parts = (sys.argv[1].split("-")[0].split("+")[0].split(".") + ["0", "0", "0"])[:3]
major, minor, patch = (int(p) for p in parts)
print(major * 10000 + minor * 100 + patch)
PY
)"

TASK="${AMANE_ANDROID_TASK:-}"
if [[ -z "$TASK" ]]; then
  if [[ -f "$KEYSTORE" ]]; then
    TASK=assembleRelease
  else
    echo "androidapp/keystore.properties not found; building a debug-signed APK" >&2
    TASK=assembleDebug
  fi
fi

(cd "$APP_DIR" && ./gradlew --console=plain \
  -PamaneVersion="$VERSION" -PamaneVersionCode="$VERSION_CODE" "$TASK")

if [[ "$TASK" == "assembleRelease" ]]; then
  APK="$APP_DIR/app/build/outputs/apk/release/app-release.apk"
else
  APK="$APP_DIR/app/build/outputs/apk/debug/app-debug.apk"
fi
[[ -f "$APK" ]] || { echo "APK missing: $APK" >&2; exit 1; }

OUT="${AMANE_ANDROID_OUT:-$ROOT/dist/Amane-$VERSION-android.apk}"
mkdir -p "$(dirname "$OUT")"
/bin/cp -f "$APK" "$OUT"

echo "APK=$OUT"
ls -lh "$OUT"
