#!/usr/bin/env bash
# One-shot macOS installer: app build → codesign → zip → drag-install .dmg →
# DMG codesign → notarize + staple → copy to the development builds folder →
# publish CheckMate-setup.dmg + version.json to Azure (Fido/checkmate/).
#
# Usage:
#   ./scripts/build_macos_release.sh [marketing-version] [--skip-azure-publish]
# Example:
#   EBC_NOTARY_PROFILE=ebraille-notary ./scripts/build_macos_release.sh 0.1.0
#
# Installer filenames always include the marketing version and build counter:
#   dist/CheckMate-macos-<version>.<build>-<arch>.dmg
#   development builds/setupcheckmate_<version>.<build>-<arch>.dmg
#   development builds/CheckMate-setup.dmg
#
# Azure Blob (after development-builds copy):
#   Fido/checkmate/CheckMate-setup.dmg and Fido/checkmate/version.json
#   Public URL: https://dl.daisy.org/tools/Fido/checkmate/CheckMate-setup.dmg
#   version.json keeps windows_latest_version and macos_latest_version separate
#   so a macOS publish merges with the existing blob and does not overwrite the
#   Windows version field. The published macos_latest_version includes the build
#   number (e.g. 0.7.42.15) to match in-app update checks.
#   CHECKMATE_SKIP_AZURE_PUBLISH=1 or --skip-azure-publish — skip upload
#   Credentials: same as Fido unlock/beta publish (az login + unlock_publish,
#   CHECKMATE_/FIDO_UNLOCK_PUBLISH_*, or FIDO_AZURE_BLOB_SAS + azcopy)
#
# Environment (all optional):
#   EBC_APP_SIGN_IDENTITY     — Developer ID Application (name or SHA-1)
#   EBC_CODESIGN_KEYCHAIN     — keychain for codesign (default: login.keychain-db)
#   EBC_ENTITLEMENTS          — entitlements plist path
#   EBC_SKIP_APPLICATION_BUILD=1 — use existing dist/CheckMate_App/
#   EBC_SKIP_APP_SIGN=1       — skip app codesign
#   EBC_SKIP_DMG_SIGN=1       — skip DMG codesign
#   EBC_SKIP_DMG_LAYOUT=1     — skip Finder layout in build_macos_dmg.sh
#   EBC_SKIP_DMG_FILE_ICON=1  — skip copying app icon onto the .dmg file
#   EBC_SKIP_NOTARY=1         — never call notarytool / stapler
#   EBC_NOTARY_PROFILE        — keychain profile from notarytool store-credentials
#   EBC_NOTARY_KEY_ID         — App Store Connect API key id
#   EBC_NOTARY_ISSUER         — API key issuer UUID
#   EBC_NOTARY_KEY            — path to AuthKey_*.p8
#   EBC_NOTARY_ALSO_SUBMIT_ZIP=1 — also notarize the zip archive
#   EBC_BUILD_NUMBER          — stamp this CFBundleVersion instead of incrementing build_counter.txt
#   EBC_SKIP_ONEDRIVE_COPY=1  — do not copy the .dmg to the dev builds folder
#   CHECKMATE_SKIP_AZURE_PUBLISH=1 — do not upload the .dmg / version.json
#   EBC_MACOS_RELEASE_ARCH_SUFFIX / EBC_NO_MACOS_RELEASE_ARCH_SUFFIX — see
#     scripts/macos_release_arch_suffix.inc.sh

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

SIGN_KEYCHAIN="${EBC_CODESIGN_KEYCHAIN:-$HOME/Library/Keychains/login.keychain-db}"
ENTITLEMENTS="${EBC_ENTITLEMENTS:-$REPO_ROOT/packaging/macos/entitlements.plist}"
APP_DIR="$REPO_ROOT/dist/CheckMate_App"
APP_BUNDLE="$APP_DIR/CheckMate.app"
APP_ID=""

read_project_version() {
  if [[ -f "$REPO_ROOT/pyproject.toml" ]]; then
    /usr/bin/awk -F'"' '/^version[[:space:]]*=/ { print $2; exit }' \
      "$REPO_ROOT/pyproject.toml"
  fi
}

SKIP_AZURE_PUBLISH=0
VERSION=""
for arg in "$@"; do
  case "$arg" in
    --skip-azure-publish)
      SKIP_AZURE_PUBLISH=1
      ;;
    -*)
      echo "ERROR: unknown option: $arg" >&2
      echo "Usage: $0 [marketing-version] [--skip-azure-publish]" >&2
      exit 1
      ;;
    *)
      VERSION="$arg"
      ;;
  esac
done
if [[ -z "$VERSION" ]]; then
  VERSION="$(read_project_version)"
fi
VERSION="${VERSION:-dev}"
if [[ "${CHECKMATE_SKIP_AZURE_PUBLISH:-}" == "1" ]]; then
  SKIP_AZURE_PUBLISH=1
fi

# shellcheck source=macos_release_arch_suffix.inc.sh
source "$REPO_ROOT/scripts/macos_release_arch_suffix.inc.sh"
# shellcheck source=macos_build_version.inc.sh
source "$REPO_ROOT/scripts/macos_build_version.inc.sh"

ARCHIVE_NAME="CheckMate-macOS-$(installer_version_tag "$VERSION")${EBC_MACOS_RELEASE_ARCH_SUFFIX}.zip"
ZIP_PATH="$REPO_ROOT/dist/$ARCHIVE_NAME"

pick_app_identity() {
  security find-identity -v -p codesigning "$SIGN_KEYCHAIN" 2>/dev/null \
    | awk '/Developer ID Application/ { print $2; exit }'
}

notary_profile_exists() {
  local profile="$1"
  [[ -n "$profile" ]] || return 1
  security find-generic-password -s "AC_PASSWORD" -a "$profile" >/dev/null 2>&1
}

# Same Apple notary login as Fido on this machine is usually "fido-notary".
# "ebraille-notary" is documented but often not stored in the keychain.
resolve_notary_profile() {
  local requested="${EBC_NOTARY_PROFILE:-${FIDO_NOTARY_PROFILE:-}}"
  if [[ -n "$requested" ]] && notary_profile_exists "$requested"; then
    echo "$requested"
    return 0
  fi
  local p
  for p in fido-notary ebraille-notary "${requested}"; do
    [[ -n "$p" ]] || continue
    if notary_profile_exists "$p"; then
      if [[ -n "$requested" && "$p" != "$requested" ]]; then
        echo "Notary profile '$requested' is not in the keychain; using '$p'." >&2
      fi
      echo "$p"
      return 0
    fi
  done
  if [[ -n "$requested" ]]; then
    echo "$requested"
    return 0
  fi
  echo ""
}

EBC_NOTARY_PROFILE="$(resolve_notary_profile)"
export EBC_NOTARY_PROFILE

notary_credentials_set() {
  [[ -n "${EBC_NOTARY_PROFILE:-}" ]] || \
    { [[ -n "${EBC_NOTARY_KEY_ID:-}" ]] && [[ -n "${EBC_NOTARY_ISSUER:-}" ]] && [[ -n "${EBC_NOTARY_KEY:-}" ]]; }
}

notarytool_submit() {
  local artifact="$1"
  if [[ -n "${EBC_NOTARY_PROFILE:-}" ]]; then
    xcrun notarytool submit "$artifact" --keychain-profile "$EBC_NOTARY_PROFILE" --wait
  else
    xcrun notarytool submit "$artifact" \
      --key "$EBC_NOTARY_KEY" \
      --key-id "$EBC_NOTARY_KEY_ID" \
      --issuer "$EBC_NOTARY_ISSUER" \
      --wait
  fi
}

# Sign every Mach-O under a directory (deepest paths first). Needed for the
# bundled Temurin JRE under Contents/runtime — codesign --deep alone is not
# always enough for notarization.
codesign_mach_o_under() {
  local dir="$1"
  local id="$2"
  local ent="$3"
  [[ -d "$dir" ]] || return 0
  local f
  local -a files=()
  while IFS= read -r -d '' f; do
    if /usr/bin/file -b "$f" 2>/dev/null | /usr/bin/grep -q '^Mach-O'; then
      files+=("$f")
    fi
  done < <(/usr/bin/find "$dir" -type f -print0 2>/dev/null)

  # Deepest first so nested libs are signed before containers that reference them.
  local sorted
  sorted="$(printf '%s\n' "${files[@]+"${files[@]}"}" | /usr/bin/awk '{ print gsub(/\//,"/",$0), $0 }' | /usr/bin/sort -nr | /usr/bin/awk '{ $1=""; sub(/^ /,""); print }')"
  while IFS= read -r f; do
    [[ -n "$f" ]] || continue
    codesign --force --options runtime --timestamp \
      --keychain "$SIGN_KEYCHAIN" \
      --entitlements "$ent" \
      --sign "$id" \
      "$f" 2>/dev/null || \
    codesign --force --options runtime --timestamp \
      --keychain "$SIGN_KEYCHAIN" \
      --sign "$id" \
      "$f"
  done <<< "$sorted"
}

# Sign nested .app / .framework / .xpc bundles deepest-first (Chrome for Testing
# helpers live under Contents/ace). Mach-O insides should already be signed.
codesign_nested_bundles_under() {
  local dir="$1"
  local id="$2"
  local ent="$3"
  [[ -d "$dir" ]] || return 0
  local b
  local sorted
  sorted="$(
    /usr/bin/find "$dir" \( -name '*.app' -o -name '*.framework' -o -name '*.xpc' -o -name '*.appex' \) -print \
      | /usr/bin/awk '{ print gsub(/\//,"/",$0), $0 }' \
      | /usr/bin/sort -nr \
      | /usr/bin/awk '{ $1=""; sub(/^ /,""); print }'
  )"
  while IFS= read -r b; do
    [[ -n "$b" ]] || continue
    codesign --force --options runtime --timestamp \
      --keychain "$SIGN_KEYCHAIN" \
      --entitlements "$ent" \
      --sign "$id" \
      "$b" 2>/dev/null || \
    codesign --force --options runtime --timestamp \
      --keychain "$SIGN_KEYCHAIN" \
      --sign "$id" \
      "$b"
  done <<< "$sorted"
}

finder_set_dmg_file_icon() {
  local dmg="$1"
  local app="${2:-$APP_BUNDLE}"
  [[ "${EBC_SKIP_DMG_FILE_ICON:-}" == "1" ]] && return 0
  [[ -f "$dmg" && -d "$app" ]] || return 0
  local _dir _name
  _dir="$(/usr/bin/dirname "$dmg")"
  _name="$(/usr/bin/basename "$dmg")"
  /usr/bin/osascript - "$_dir" "$_name" "$app" <<'OSA' || true
on run argv
  set folderPath to item 1 of argv
  set dmgName to item 2 of argv
  set appPath to item 3 of argv
  tell application "Finder"
    activate
    delay 0.3
    set dest to POSIX file folderPath as alias
    set dmgItem to item dmgName of folder dest
    set appRef to POSIX file appPath as alias
    try
      set icon of dmgItem to icon of appRef
    end try
  end tell
end run
OSA
}

echo "=== 1/5 Application build (PyInstaller + zip) ==="
echo "Release artifact arch suffix: '${EBC_MACOS_RELEASE_ARCH_SUFFIX}'"
if [[ "${EBC_SKIP_APPLICATION_BUILD:-}" == "1" ]]; then
  echo "Skipped (EBC_SKIP_APPLICATION_BUILD=1) — using existing $APP_DIR"
  if [[ ! -d "$APP_BUNDLE" ]]; then
    echo "ERROR: $APP_BUNDLE missing."
    exit 1
  fi
else
  chmod +x "$REPO_ROOT/scripts/build_macos.sh"
  "$REPO_ROOT/scripts/build_macos.sh" "$VERSION"
fi

SETUP_VER="$VERSION"
if [[ -f "$APP_BUNDLE/Contents/Info.plist" ]]; then
  PLIST_VER="$(/usr/bin/plutil -extract CFBundleShortVersionString raw "$APP_BUNDLE/Contents/Info.plist" 2>/dev/null || true)"
  [[ -n "$PLIST_VER" ]] && SETUP_VER="$PLIST_VER"
fi

if [[ "${EBC_SKIP_APP_SIGN:-}" == "1" ]]; then
  echo "=== 2/5 Skipping app codesign (EBC_SKIP_APP_SIGN=1) ==="
else
  if [[ ! -f "$ENTITLEMENTS" ]]; then
    echo "ERROR: Entitlements not found: $ENTITLEMENTS"
    exit 1
  fi
  APP_ID="${EBC_APP_SIGN_IDENTITY:-$(pick_app_identity)}"
  if [[ -z "$APP_ID" ]]; then
    echo "ERROR: No Developer ID Application identity. Set EBC_APP_SIGN_IDENTITY or install the cert."
    exit 1
  fi
  echo "=== 2/5 Codesign application (identity: $APP_ID) ==="
  if [[ ! -d "$APP_BUNDLE" ]]; then
    echo "ERROR: $APP_BUNDLE missing after build."
    exit 1
  fi

  echo "Signing Mach-O files under Contents/runtime (bundled JRE)…"
  codesign_mach_o_under "$APP_BUNDLE/Contents/runtime" "$APP_ID" "$ENTITLEMENTS"

  echo "Signing remaining Mach-O under Contents (PyInstaller / frameworks)…"
  codesign_mach_o_under "$APP_BUNDLE/Contents/Frameworks" "$APP_ID" "$ENTITLEMENTS"
  codesign_mach_o_under "$APP_BUNDLE/Contents/MacOS" "$APP_ID" "$ENTITLEMENTS"
  # Resources often holds _internal / .so for onedir bundles
  if [[ -d "$APP_BUNDLE/Contents/Resources" ]]; then
    codesign_mach_o_under "$APP_BUNDLE/Contents/Resources" "$APP_ID" "$ENTITLEMENTS"
  fi
  # Ace (Node + Chromium) and other tool trees were previously unsigned;
  # Apple notary rejects ad-hoc Chrome helpers and unsigned .bare addons.
  for extra in ace checker epubcheck verapdf; do
    if [[ -d "$APP_BUNDLE/Contents/$extra" ]]; then
      echo "Signing Mach-O under Contents/${extra}..."
      codesign_mach_o_under "$APP_BUNDLE/Contents/$extra" "$APP_ID" "$ENTITLEMENTS"
    fi
  done
  if [[ -d "$APP_BUNDLE/Contents/ace" ]]; then
    echo "Signing nested Chrome/Ace bundles under Contents/ace…"
    codesign_nested_bundles_under "$APP_BUNDLE/Contents/ace" "$APP_ID" "$ENTITLEMENTS"
  fi

  # Sign the bundle last, without --deep: nested Mach-O is already signed
  # above, and --deep treats extensionless data (e.g. tiktoken BPE cache
  # under Frameworks) as unsigned code and fails.
  codesign --force --options runtime --timestamp \
    --keychain "$SIGN_KEYCHAIN" \
    --entitlements "$ENTITLEMENTS" \
    --sign "$APP_ID" \
    "$APP_BUNDLE"

  codesign --verify --verbose=2 "$APP_BUNDLE"
  echo "Refreshing dist zip so it contains the signed app…"
  # Prefer version from build_macos naming; rebuild zip name if SETUP_VER differs
  ARCHIVE_NAME="CheckMate-macOS-$(installer_version_tag "$SETUP_VER")${EBC_MACOS_RELEASE_ARCH_SUFFIX}.zip"
  ZIP_PATH="$REPO_ROOT/dist/$ARCHIVE_NAME"
  rm -f "$ZIP_PATH"
  (cd dist && zip -qr "$ARCHIVE_NAME" CheckMate_App)
  echo "Updated: $ZIP_PATH"
fi

echo "=== 3/5 Disk image (.dmg) ==="
chmod +x "$REPO_ROOT/scripts/build_macos_dmg.sh"
"$REPO_ROOT/scripts/build_macos_dmg.sh" "$SETUP_VER"

INSTALLER_TAG="$(installer_version_tag "$SETUP_VER")"
DMG="$REPO_ROOT/dist/CheckMate-macos-${INSTALLER_TAG}${EBC_MACOS_RELEASE_ARCH_SUFFIX}.dmg"
if [[ ! -f "$DMG" ]]; then
  echo "WARNING: Expected dmg not found at $DMG"
else
  if [[ -z "$APP_ID" ]] || [[ "${EBC_SKIP_DMG_SIGN:-}" == "1" ]]; then
    if [[ -z "$APP_ID" ]]; then
      echo "=== 4/5 Skipping DMG codesign (app was not signed) ==="
    else
      echo "=== 4/5 Skipping DMG codesign (EBC_SKIP_DMG_SIGN=1) ==="
    fi
  else
    echo "=== 4/5 Sign disk image (identity: $APP_ID) ==="
    codesign --sign "$APP_ID" --timestamp --keychain "$SIGN_KEYCHAIN" "$DMG"
    codesign --verify --verbose=2 "$DMG"
    echo "Signed: $DMG"
    finder_set_dmg_file_icon "$DMG"
  fi
fi

echo "=== 5/5 Notarization ==="
if [[ "${EBC_SKIP_NOTARY:-}" == "1" ]]; then
  echo "Skipped (EBC_SKIP_NOTARY=1)."
elif ! notary_credentials_set; then
  echo "Skipped — no notary credentials in the environment."
  echo "  To automate: set EBC_NOTARY_PROFILE (keychain, often fido-notary) or"
  echo "  EBC_NOTARY_KEY + EBC_NOTARY_KEY_ID + EBC_NOTARY_ISSUER (API key)."
  echo "  Manual after this script:"
  echo "    xcrun notarytool submit \"$DMG\" --keychain-profile \"YOUR_PROFILE\" --wait"
  echo "    xcrun stapler staple \"$DMG\""
elif [[ ! -f "$DMG" ]]; then
  echo "Skipped — .dmg not found (cannot notarize)."
elif ! codesign -v "$DMG" 2>/dev/null; then
  echo "ERROR: Notarization requires a signed .dmg."
  echo "  Fix: sign without EBC_SKIP_APP_SIGN / EBC_SKIP_DMG_SIGN."
  exit 1
else
  echo "Submitting signed dmg to Apple notary service (notarytool --wait)…"
  if [[ -n "${EBC_NOTARY_PROFILE:-}" ]]; then
    echo "  keychain profile: $EBC_NOTARY_PROFILE"
  fi
  NOTARY_TMP="$(mktemp)"
  set +e
  if [[ -n "${EBC_NOTARY_PROFILE:-}" ]]; then
    xcrun notarytool submit "$DMG" --keychain-profile "$EBC_NOTARY_PROFILE" --wait 2>&1 | tee "$NOTARY_TMP"
    NOTARY_RC=${PIPESTATUS[0]}
  else
    xcrun notarytool submit "$DMG" \
      --key "$EBC_NOTARY_KEY" \
      --key-id "$EBC_NOTARY_KEY_ID" \
      --issuer "$EBC_NOTARY_ISSUER" \
      --wait 2>&1 | tee "$NOTARY_TMP"
    NOTARY_RC=${PIPESTATUS[0]}
  fi
  set -e
  if [[ "$NOTARY_RC" -ne 0 ]] || grep -q 'status: Invalid' "$NOTARY_TMP"; then
    rm -f "$NOTARY_TMP"
    echo "ERROR: Notarization did not succeed (no ticket to staple)."
    echo "  Inspect: xcrun notarytool log <submission-id> --keychain-profile \"${EBC_NOTARY_PROFILE:-YOUR_PROFILE}\""
    exit 1
  fi
  if ! grep -q 'status: Accepted' "$NOTARY_TMP"; then
    rm -f "$NOTARY_TMP"
    echo "ERROR: notarytool finished without Accepted status; refusing to staple."
    exit 1
  fi
  rm -f "$NOTARY_TMP"
  echo "Stapling notarization ticket to the dmg…"
  xcrun stapler staple "$DMG"
  echo "Notarized + stapled: $DMG"
  finder_set_dmg_file_icon "$DMG"

  if [[ "${EBC_NOTARY_ALSO_SUBMIT_ZIP:-}" == "1" ]]; then
    if [[ ! -f "$ZIP_PATH" ]]; then
      echo "WARNING: EBC_NOTARY_ALSO_SUBMIT_ZIP=1 but zip missing: $ZIP_PATH"
    else
      echo "Submitting zip to notary (EBC_NOTARY_ALSO_SUBMIT_ZIP=1)…"
      notarytool_submit "$ZIP_PATH"
      xcrun stapler staple "$ZIP_PATH"
      echo "Notarized + stapled: $ZIP_PATH"
    fi
  fi
fi

# Copy dmg to shared OneDrive development builds (version + build in the filename).
DEFAULT_DEV_BUILDS_DIR="$HOME/Library/CloudStorage/OneDrive-SharedLibraries-DAISYConsortium/Shared Projects - Documents/Exploring AI/Experimentation app/development builds"
DEV_BUILDS_DIR="${EBC_DEV_BUILDS_DIR:-$DEFAULT_DEV_BUILDS_DIR}"
VERSION_JSON="$REPO_ROOT/installer/Output/version.json"
if [[ "${EBC_SKIP_ONEDRIVE_COPY:-}" == "1" ]]; then
  echo "Skipping OneDrive dev copy (EBC_SKIP_ONEDRIVE_COPY=1)."
elif [[ ! -f "$DMG" ]]; then
  echo "ERROR: Cannot copy installer — dmg not found: $DMG"
  exit 1
else
  SETUP_NAME="setupcheckmate_${INSTALLER_TAG}${EBC_MACOS_RELEASE_ARCH_SUFFIX}.dmg"
  echo "Copying disk image to development builds: $DEV_BUILDS_DIR/$SETUP_NAME"
  mkdir -p "$DEV_BUILDS_DIR"
  cp -f "$DMG" "$DEV_BUILDS_DIR/$SETUP_NAME"
  cp -f "$DMG" "$DEV_BUILDS_DIR/CheckMate-setup.dmg"
  echo "Copied: $DEV_BUILDS_DIR/$SETUP_NAME and CheckMate-setup.dmg"
fi

# Upload CheckMate-setup.dmg and version.json next to Fido betas (Fido/checkmate/).
mkdir -p "$(dirname "$VERSION_JSON")"
if [[ "$SKIP_AZURE_PUBLISH" -eq 1 ]]; then
  reason="--skip-azure-publish"
  if [[ "${CHECKMATE_SKIP_AZURE_PUBLISH:-}" == "1" ]]; then
    reason="CHECKMATE_SKIP_AZURE_PUBLISH=1"
  fi
  echo "Skipping Azure publish ($reason)"
  echo "=== Writing local version.json (no Azure upload) ==="
  uv run --extra dev python scripts/publish_installer_azure.py \
    --platform macos \
    --version "$INSTALLER_TAG" \
    --write-version-json "$VERSION_JSON" \
    --skip-upload
elif [[ ! -f "$DMG" ]]; then
  echo "ERROR: Cannot publish to Azure — dmg not found: $DMG"
  exit 1
else
  echo "=== Publishing installer and version.json to Azure Blob (Fido/checkmate/) ==="
  uv run --extra dev python scripts/publish_installer_azure.py \
    --platform macos \
    --setup-dmg "$DMG" \
    --version "$INSTALLER_TAG" \
    --write-version-json "$VERSION_JSON"
fi

if [[ "${EBC_SKIP_ONEDRIVE_COPY:-}" != "1" && -f "$VERSION_JSON" && -d "$DEV_BUILDS_DIR" ]]; then
  cp -f "$VERSION_JSON" "$DEV_BUILDS_DIR/checkmate-version.json"
  echo "Copied checkmate-version.json to: $DEV_BUILDS_DIR"
fi

echo ""
echo "Done. App folder: $APP_DIR"
echo "  Zip: $ZIP_PATH"
echo "  Dmg: $DMG"
