#!/usr/bin/env bash
# Build CheckMate for macOS: PyInstaller .app, stage folder, zip.
#
# Usage: ./scripts/build_macos.sh [version]
# Example: ./scripts/build_macos.sh 0.1.0
#
# Output:
#   dist/CheckMate_App/CheckMate.app
#   dist/CheckMate-macOS-<version>.<build>-<arch>.zip
#
# Each run increments build_counter.txt and stamps CFBundleVersion so a new DMG
# can replace an older build of the same marketing version in /Applications.

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# shellcheck source=macos_release_arch_suffix.inc.sh
source "$REPO_ROOT/scripts/macos_release_arch_suffix.inc.sh"
# shellcheck source=macos_build_version.inc.sh
source "$REPO_ROOT/scripts/macos_build_version.inc.sh"

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

if [[ -n "${EBC_BUILD_NUMBER:-}" ]]; then
  BUILD_NUMBER="$EBC_BUILD_NUMBER"
  write_build_counter "$BUILD_NUMBER"
else
  BUILD_NUMBER="$(next_build_number)"
fi

APP_NAME="CheckMate"
APP_DIR="dist/CheckMate_App"
ARCHIVE_NAME="CheckMate-macOS-$(installer_version_tag "$VERSION")${EBC_MACOS_RELEASE_ARCH_SUFFIX}.zip"

echo "Building CheckMate for macOS"
echo "  version: $VERSION"
echo "  build:   $BUILD_NUMBER"
echo "  arch suffix: '${EBC_MACOS_RELEASE_ARCH_SUFFIX}'"

echo "==> Packaging with PyInstaller (scripts/package.py)…"
uv sync --extra dev
uv run python scripts/package.py --clean --build-number "$BUILD_NUMBER"

if [[ ! -d "dist/${APP_NAME}.app" ]]; then
  echo "ERROR: dist/${APP_NAME}.app not found after package.py."
  exit 1
fi

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
cp -R "dist/${APP_NAME}.app" "$APP_DIR/"

echo "App folder: $APP_DIR/${APP_NAME}.app"
echo "  CFBundleShortVersionString=$VERSION  CFBundleVersion=$BUILD_NUMBER"

rm -f "dist/$ARCHIVE_NAME"
(cd dist && zip -qr "$ARCHIVE_NAME" CheckMate_App)
echo "Created: dist/$ARCHIVE_NAME"
