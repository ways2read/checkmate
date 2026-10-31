# Sourced by macOS packaging scripts for CFBundleVersion (build number).
#
# CFBundleShortVersionString = marketing version (pyproject.toml / arg).
# CFBundleVersion = integer from build_counter.txt (bumped by the Windows build,
# never here) so each DMG can
# replace an older build of the same marketing version in /Applications.

EBC_BUILD_COUNTER_FILE="${EBC_BUILD_COUNTER_FILE:-$REPO_ROOT/build_counter.txt}"

read_build_counter() {
  if [[ -f "$EBC_BUILD_COUNTER_FILE" ]]; then
    tr -d '\r\n' < "$EBC_BUILD_COUNTER_FILE" | head -c 20
  else
    echo "0"
  fi
}

write_build_counter() {
  printf '%s\n' "$1" > "$EBC_BUILD_COUNTER_FILE"
}

# Filename stem: marketing version plus build counter (e.g. 0.7.32.8).
installer_version_tag() {
  local ver="${1:-dev}"
  local bc
  bc="$(read_build_counter)"
  if [[ ! "$bc" =~ ^[0-9]+$ ]]; then
    bc="0"
  fi
  printf '%s.%s' "$ver" "$bc"
}
