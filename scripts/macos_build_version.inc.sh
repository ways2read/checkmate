# Sourced by macOS packaging scripts.
#
# Public version is x.y.z from the repo (pyproject.toml / __init__.py), the
# same string Windows publishes. Do not append build_counter.txt.
#
# CFBundleShortVersionString = marketing version (x.y.z).
# CFBundleVersion = encoded integer from that version (0.7.43 -> 743) so
# Finder treats a new DMG as newer than older integer-only build_counter
# stamps. Override with EBC_BUILD_NUMBER if needed.

# Filename stem: marketing version only (e.g. 0.7.43).
installer_version_tag() {
  printf '%s' "${1:-dev}"
}

# Encode x.y.z as a monotonic CFBundleVersion integer.
bundle_build_from_version() {
  local ver="${1:-0.0.0}"
  local major=0 minor=0 patch=0
  IFS='.' read -r major minor patch _ <<< "${ver}."
  [[ "$major" =~ ^[0-9]+$ ]] || major=0
  [[ "$minor" =~ ^[0-9]+$ ]] || minor=0
  [[ "$patch" =~ ^[0-9]+$ ]] || patch=0
  echo $((major * 10000 + minor * 100 + patch))
}
