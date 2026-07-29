#!/usr/bin/env bash
# Build a drag-to-Applications disk image for eBraille Checker GUI.
# Prerequisites: dist/eBrailleChecker_App/eBrailleChecker.app
#   (run ./scripts/build_macos.sh first, or the release script).
#
# Background: packaging/macos/dmg_background.png (660×400).
# Icon centres (logical points): app ≈ (165, 195), Applications ≈ (495, 195).
#
# Usage:  ./scripts/build_macos_dmg.sh [marketing-version]
# Output: dist/eBrailleCheckerGUI-macos-<version>-<arch>.dmg

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

MARKETING_VER="${1:-}"
if [[ -z "$MARKETING_VER" ]]; then
  MARKETING_VER="$(read_project_version)"
fi
MARKETING_VER="${MARKETING_VER:-dev}"

APP_BUNDLE="$REPO_ROOT/dist/eBrailleChecker_App/eBrailleChecker.app"
if [[ ! -d "$APP_BUNDLE" ]]; then
  echo "ERROR: $APP_BUNDLE not found. Run ./scripts/build_macos.sh first."
  exit 1
fi

BG="${EBC_DMG_BACKGROUND:-$REPO_ROOT/packaging/macos/dmg_background.png}"
if [[ ! -f "$BG" ]]; then
  echo "ERROR: DMG background image missing: $BG"
  echo "  Run: python3 packaging/macos/make_dmg_background.py"
  exit 1
fi

WIN_W=660
WIN_H=400
APP_X=165
APP_Y=195
APPS_X=495
APPS_Y=195

DMG_RW="$REPO_ROOT/dist/eBrailleCheckerGUI-macos-${MARKETING_VER}${EBC_MACOS_RELEASE_ARCH_SUFFIX}.rw.dmg"
DMG_OUT="$REPO_ROOT/dist/eBrailleCheckerGUI-macos-${MARKETING_VER}${EBC_MACOS_RELEASE_ARCH_SUFFIX}.dmg"
VOL_LABEL="eBraille Checker"
MOUNT_PT=""

APP_SZ="$(/usr/bin/du -sm "$APP_BUNDLE" | /usr/bin/awk '{print $1}')"
SIZE_MB=$((APP_SZ + 64))
[[ "$SIZE_MB" -lt 256 ]] && SIZE_MB=256

rm -f "$DMG_RW" "$DMG_OUT"

/usr/bin/hdiutil create -volname "$VOL_LABEL" -ov -fs HFS+ -size "${SIZE_MB}m" -type UDIF "$DMG_RW"

cleanup() {
  if [[ -n "$MOUNT_PT" ]] && [[ -d "$MOUNT_PT" ]]; then
    /usr/bin/hdiutil detach "$MOUNT_PT" -force || true
  fi
}
trap cleanup EXIT

MOUNT_PT="$(
  /usr/bin/hdiutil attach "$DMG_RW" \
    | /usr/bin/awk -F'\t' '/\/Volumes\// {sub(/[[:space:]]+$/, "", $NF); print $NF}'
)"

if [[ -z "$MOUNT_PT" ]]; then
  echo "ERROR: could not determine mount point from hdiutil attach output."
  exit 1
fi

/bin/mkdir -p "$MOUNT_PT/.background"

BG_W="$(/usr/bin/sips -g pixelWidth  "$BG" 2>/dev/null | /usr/bin/awk '/pixelWidth:/  {print $2}')"
BG_H="$(/usr/bin/sips -g pixelHeight "$BG" 2>/dev/null | /usr/bin/awk '/pixelHeight:/ {print $2}')"

if [[ "$BG_W" == "660" && "$BG_H" == "400" ]]; then
  /bin/cp "$BG" "$MOUNT_PT/.background/background.png"
else
  echo "WARNING: Background is ${BG_W}×${BG_H} px; expected 660×400. Resizing."
  /usr/bin/sips --resampleHeightWidth 400 660 "$BG" --out "$MOUNT_PT/.background/background.png"
fi

/bin/cp -R "$APP_BUNDLE" "$MOUNT_PT/"
/bin/ln -sf /Applications "$MOUNT_PT/Applications"

README_SRC="${EBC_DMG_README:-$REPO_ROOT/packaging/macos/dmg_README.txt}"
if [[ -f "$README_SRC" ]]; then
  /bin/cp "$README_SRC" "$MOUNT_PT/Install eBraille Checker.txt"
fi

if [[ -f "$REPO_ROOT/installer/eBrailleChecker.icns" ]]; then
  /bin/cp "$REPO_ROOT/installer/eBrailleChecker.icns" "$MOUNT_PT/.VolumeIcon.icns"
  /usr/bin/SetFile -a C "$MOUNT_PT" 2>/dev/null || true
fi

if [[ "${EBC_SKIP_DMG_LAYOUT:-}" != "1" ]]; then
  VOL_NAME="$(/usr/bin/basename "$MOUNT_PT")"
  _L=120
  _T=120
  _R=$((_L + WIN_W))
  _B=$((_T + WIN_H + 28))

  /usr/bin/osascript \
    - "$VOL_NAME" "$MOUNT_PT/.background/background.png" \
      "$_L" "$_T" "$_R" "$_B" \
      "$APP_X" "$APP_Y" "$APPS_X" "$APPS_Y" <<'OSA' || true
on run argv
  set volName to item 1 of argv
  set bgPath  to item 2 of argv
  set L to (item 3 of argv) as integer
  set T to (item 4 of argv) as integer
  set R to (item 5 of argv) as integer
  set B to (item 6 of argv) as integer
  set appX  to (item 7 of argv) as integer
  set appY  to (item 8 of argv) as integer
  set appsX to (item 9 of argv) as integer
  set appsY to (item 10 of argv) as integer
  tell application "Finder"
    tell disk volName
      open
      delay 0.6
      set win to container window
      set current view of win to icon view
      set toolbar visible of win to false
      set statusbar visible of win to false
      set bounds of win to {L, T, R, B}
      set background picture of icon view options of win to (POSIX file bgPath as alias)
      set arrangement of icon view options of win to not arranged
      set icon size of icon view options of win to 96
      set position of item "eBrailleChecker.app" of win to {appX, appY}
      set position of item "Applications" of win to {appsX, appsY}
      update without registering applications
      delay 0.5
    end tell
  end tell
end run
OSA
fi

/usr/bin/hdiutil detach "$MOUNT_PT"
MOUNT_PT=""
trap - EXIT

/usr/bin/hdiutil convert "$DMG_RW" -format UDZO -imagekey zlib-level=9 -o "$DMG_OUT"
/bin/rm -f "$DMG_RW"

if [[ "${EBC_SKIP_DMG_FILE_ICON:-}" != "1" ]]; then
  /usr/bin/osascript \
    - "$(/usr/bin/dirname "$DMG_OUT")" "$(/usr/bin/basename "$DMG_OUT")" \
      "$APP_BUNDLE" <<'OSA' || true
on run argv
  tell application "Finder"
    activate
    delay 0.3
    set dest    to POSIX file (item 1 of argv) as alias
    set dmgItem to item (item 2 of argv) of folder dest
    try
      set icon of dmgItem to icon of (POSIX file (item 3 of argv) as alias)
    end try
  end tell
end run
OSA
fi

echo ""
echo "Created: $DMG_OUT"
echo "Open the .dmg and drag eBrailleChecker.app onto Applications."
