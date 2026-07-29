# Sourced by macOS packaging scripts for CFBundleVersion (build number).
#
# CFBundleShortVersionString = marketing version (pyproject.toml / arg).
# CFBundleVersion = monotonic integer from build_counter.txt so each DMG can
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

# Increment build_counter.txt and print the new value.
next_build_number() {
  local current next
  current="$(read_build_counter)"
  if [[ "$current" =~ ^[0-9]+$ ]]; then
    next=$((current + 1))
  else
    next=1
  fi
  write_build_counter "$next"
  echo "$next"
}
