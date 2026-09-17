#!/usr/bin/env bash
# Android-only: build the WebView shell APK (Kotlin + Gradle).
# Not for macOS/Windows packaging. Expects: JDK 17+ and an Android SDK
# (ANDROID_SDK_ROOT / ANDROID_HOME, or androidapp/local.properties).
# Prefer: just android-app
#
# Env:
#   AMANE_ANDROID_OUT   output APK path (default: dist/Amane-app-<version>.apk)
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

VERSION="$(tr -d '[:space:]' < "$APP_DIR/version.txt")"
[[ -n "$VERSION" ]] || { echo "empty androidapp/version.txt" >&2; exit 1; }

TASK="${AMANE_ANDROID_TASK:-}"
if [[ -z "$TASK" ]]; then
  if [[ -f "$KEYSTORE" ]]; then
    TASK=assembleRelease
  else
    echo "androidapp/keystore.properties not found; building a debug-signed APK" >&2
    TASK=assembleDebug
  fi
fi

(cd "$APP_DIR" && ./gradlew --console=plain "$TASK")

if [[ "$TASK" == "assembleRelease" ]]; then
  APK="$APP_DIR/app/build/outputs/apk/release/app-release.apk"
else
  APK="$APP_DIR/app/build/outputs/apk/debug/app-debug.apk"
fi
[[ -f "$APK" ]] || { echo "APK missing: $APK" >&2; exit 1; }

OUT="${AMANE_ANDROID_OUT:-$ROOT/dist/Amane-app-$VERSION.apk}"
mkdir -p "$(dirname "$OUT")"
/bin/cp -f "$APK" "$OUT"

echo "APK=$OUT"
ls -lh "$OUT"
