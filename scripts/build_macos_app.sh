#!/usr/bin/env bash
# macOS-only: build Amane.app with PyInstaller (onedir + thin .app wrapper).
# Not for Windows/Linux. Expects: Darwin, .venv with pyinstaller, web/dist built.
# Prefer: just macos-app (sync + frontend build + this script).
#
# Env:
#   AMANE_MACOS_APP_OUT   output .app path (default: dist/Amane.app)
#   AMANE_MACOS_APP_WORK  work dir (default: dist/macos-app-work)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "scripts/build_macos_app.sh is macOS-only (.app packaging)" >&2
  exit 1
fi

OUT="${AMANE_MACOS_APP_OUT:-$ROOT/dist/Amane.app}"
WORK="${AMANE_MACOS_APP_WORK:-$ROOT/dist/macos-app-work}"
NAME="$(basename "$OUT" .app)"

if [[ ! -x .venv/bin/pyinstaller ]]; then
  echo "pyinstaller missing; run: just sync" >&2
  exit 1
fi

if [[ ! -f web/dist/index.html ]]; then
  echo "web/dist missing; run: just build" >&2
  exit 1
fi

if [[ ! -f "$ROOT/assets/app.icns" ]]; then
  echo "assets/app.icns missing; run: just icons" >&2
  exit 1
fi

rm -rf "$WORK" "$OUT"
mkdir -p "$WORK" "$(dirname "$OUT")"

# Excludes: patchright (lazy browser import in net/http.py) and IPython
# (python-dotenv -> dotenv.ipython -> IPython.core.magic).
.venv/bin/pyinstaller \
  --noconfirm --clean --onedir --console \
  --name "$NAME" \
  --paths "$ROOT/src" \
  --add-data "$ROOT/web/dist:web/dist" \
  --add-data "$ROOT/src/amane/db/migrations:amane/db/migrations" \
  --add-data "$ROOT/src/amane/media/watermarks:amane/media/watermarks" \
  --add-data "$ROOT/alembic.ini:." \
  --collect-submodules amane \
  --exclude-module patchright \
  --exclude-module IPython \
  --collect-all pydantic_ai \
  --collect-all pydantic_graph \
  --collect-all genai_prices \
  --hidden-import socksio \
  --copy-metadata genai_prices \
  --copy-metadata pydantic_ai_slim \
  --copy-metadata amane \
  --distpath "$WORK/dist" \
  --workpath "$WORK/build" \
  --specpath "$WORK" \
  "$ROOT/scripts/pyinstaller_entry.py"

ONEDIR="$WORK/dist/$NAME"
[[ -d "$ONEDIR" ]] || { echo "PyInstaller onedir missing: $ONEDIR" >&2; exit 1; }

# Thin .app wrapper — keeps onedir intact so relative dylibs resolve.
mkdir -p "$OUT/Contents/MacOS" "$OUT/Contents/Resources"
cp -R "$ONEDIR" "$OUT/Contents/Resources/onedir"
mkdir -p "$OUT/Contents/Resources/web"
cp -R "$ROOT/web/dist" "$OUT/Contents/Resources/web/dist"

if [[ ! -x "$OUT/Contents/Resources/onedir/$NAME" ]]; then
  BIN="$(find "$OUT/Contents/Resources/onedir" -maxdepth 1 -type f -perm -111 | head -n 1)"
  [[ -n "$BIN" ]] || { echo "no executable in onedir" >&2; exit 1; }
fi

# Swift is CFBundleExecutable (Launch Services identity + supervise + menu bar).
if ! command -v swift >/dev/null 2>&1; then
  echo "swift required to build Amane.app" >&2
  exit 1
fi
(cd "$ROOT/macapp" && swift build -c release --quiet)
/bin/cp -f "$ROOT/macapp/.build/release/Amane" "$OUT/Contents/MacOS/Amane"
chmod +x "$OUT/Contents/MacOS/Amane"
/bin/cp -f "$ROOT/assets/app.icns" "$OUT/Contents/Resources/AppIcon.icns"
UI_APP="$OUT/Contents/Resources/AmaneUI.app"
mkdir -p "$UI_APP/Contents/MacOS"
/bin/cp -f "$ROOT/macapp/.build/release/AmaneUI" "$UI_APP/Contents/MacOS/AmaneUI"
chmod +x "$UI_APP/Contents/MacOS/AmaneUI"
cat > "$UI_APP/Contents/Info.plist" <<'UIEOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>AmaneUI</string>
  <key>CFBundleIdentifier</key>
  <string>com.github.sqzw-x.amane.ui</string>
  <key>CFBundleName</key>
  <string>AmaneUI</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>LSUIElement</key>
  <true/>
</dict>
</plist>
UIEOF
echo "EXE=$OUT/Contents/MacOS/Amane"
echo "UI=$UI_APP"

APP_VERSION="$(.venv/bin/python -c 'from amane.version import get_version; print(get_version())')"

cat > "$OUT/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>Amane</string>
  <key>CFBundleIdentifier</key>
  <string>com.github.sqzw-x.amane</string>
  <key>CFBundleName</key>
  <string>${NAME}</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>${APP_VERSION}</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>LSUIElement</key>
  <true/>
  <key>LSMultipleInstancesProhibited</key>
  <true/>
</dict>
</plist>
EOF

echo "APP=$OUT"
du -sh "$OUT"
