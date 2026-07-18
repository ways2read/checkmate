#!/usr/bin/env bash
# Build eBraille Checker GUI for macOS: PyInstaller .app, stage folder, zip.
#
# Usage: ./scripts/build_macos.sh [version]
# Example: ./scripts/build_macos.sh 0.1.0
#
# Output:
#   dist/eBrailleChecker_App/eBrailleChecker.app
#   dist/eBrailleCheckerGUI-macOS-<version>-<arch>.zip

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# shellcheck source=macos_release_arch_suffix.inc.sh
source "$REPO_ROOT/scripts/macos_release_arch_suffix.inc.sh"

read_project_version() {
  if [[ -f "$REPO_ROOT/pyproject.toml" ]]; then
    /usr/bin/awk -F'"' '/^version[[:space:]]*=/ { print $2; exit }' \
      "$REPO_ROOT/pyproject.toml"
  fi
}

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
  VERSION="$(read_project_version)"
fi
VERSION="${VERSION:-dev}"

APP_NAME="eBrailleChecker"
APP_DIR="dist/eBrailleChecker_App"
ARCHIVE_NAME="eBrailleCheckerGUI-macOS-${VERSION}${EBC_MACOS_RELEASE_ARCH_SUFFIX}.zip"

echo "Building eBraille Checker GUI for macOS"
echo "  version: $VERSION"
echo "  arch suffix: '${EBC_MACOS_RELEASE_ARCH_SUFFIX}'"

if [[ ! -f "installer/eBrailleChecker.icns" ]]; then
  echo "==> Creating installer/eBrailleChecker.icns from icon.png…"
  uv run python scripts/make_icns.py
fi

echo "==> Packaging with PyInstaller (scripts/package.py)…"
uv sync --extra dev
uv run python scripts/package.py --clean

if [[ ! -d "dist/${APP_NAME}.app" ]]; then
  echo "ERROR: dist/${APP_NAME}.app not found after package.py."
  exit 1
fi

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
cp -R "dist/${APP_NAME}.app" "$APP_DIR/"

# Stamp marketing version into the staged app (package.py also sets this).
INFO_PLIST="$APP_DIR/${APP_NAME}.app/Contents/Info.plist"
if [[ -f "$INFO_PLIST" ]]; then
  /usr/bin/plutil -replace CFBundleShortVersionString -string "$VERSION" "$INFO_PLIST" 2>/dev/null || true
  /usr/bin/plutil -replace CFBundleVersion -string "$VERSION" "$INFO_PLIST" 2>/dev/null || true
fi

echo "App folder: $APP_DIR/${APP_NAME}.app"

rm -f "dist/$ARCHIVE_NAME"
(cd dist && zip -qr "$ARCHIVE_NAME" eBrailleChecker_App)
echo "Created: dist/$ARCHIVE_NAME"
