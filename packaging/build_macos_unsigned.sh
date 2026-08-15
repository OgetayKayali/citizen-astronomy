#!/usr/bin/env bash
# Build an unsigned Citizen Astronomy .app zip for macOS alpha testers.
# Must run on macOS with the same large texture/tile assets required by the Windows alpha build.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script must run on macOS." >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT/.venv/bin/python"
  else
    PYTHON_BIN="$(command -v python3)"
  fi
fi

DIST_DIR="${DIST_DIR:-$ROOT/_tmp_macos_unsigned_dist}"
BUILD_DIR="${BUILD_DIR:-$ROOT/_tmp_macos_unsigned_build}"
ZIP_DIR="${ZIP_DIR:-$ROOT/packaging/dist/macos-unsigned}"
APP_NAME="Citizen Astronomy.app"
ZIP_NAME="CitizenAstronomy-macos-unsigned.zip"

echo "Using Python: $PYTHON_BIN"
"$PYTHON_BIN" -c "import sys; assert sys.version_info >= (3, 11), sys.version"

echo "Ensuring packaging dependencies..."
"$PYTHON_BIN" -m pip install -e .
"$PYTHON_BIN" -m pip install "pyinstaller>=6.0"

echo "Preparing macOS .icns (best effort)..."
"$PYTHON_BIN" "$ROOT/packaging/prepare_macos_icon.py"

echo "Generating smoke fixtures (if needed)..."
"$PYTHON_BIN" "$ROOT/packaging/generate_smoke_fixtures.py"

echo "Running PyInstaller..."
rm -rf "$DIST_DIR" "$BUILD_DIR"
"$PYTHON_BIN" -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "$DIST_DIR" \
  --workpath "$BUILD_DIR" \
  "$ROOT/CitizenAstronomyMacUnsigned.spec"

APP_PATH="$DIST_DIR/$APP_NAME"
if [[ ! -d "$APP_PATH" ]]; then
  echo "Expected app bundle missing: $APP_PATH" >&2
  exit 1
fi

mkdir -p "$ZIP_DIR"
ZIP_PATH="$ZIP_DIR/$ZIP_NAME"
rm -f "$ZIP_PATH"

echo "Creating zip with ditto (preserves macOS metadata)..."
ditto -c -k --keepParent "$APP_PATH" "$ZIP_PATH"

# Clear quarantine on the local build so the builder can smoke-test launch.
xattr -dr com.apple.quarantine "$APP_PATH" 2>/dev/null || true

echo
echo "Unsigned macOS build ready:"
echo "  App: $APP_PATH"
echo "  Zip: $ZIP_PATH"
echo
echo "Give testers the zip. First open: right-click app → Open → Open."
echo "Optional smoke:"
echo "  \"$APP_PATH/Contents/MacOS/CitizenAstronomy\" --packaged-format-smoke --packaged-format-smoke-fixtures packaging/fixtures"
