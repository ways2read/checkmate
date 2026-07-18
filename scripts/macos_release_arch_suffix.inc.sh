# Sourced by macOS build/packaging scripts so release artifacts show which CPU
# they were built for.
#
# Default (when EBC_NO_MACOS_RELEASE_ARCH_SUFFIX is not 1):
#   x86_64  -> "-Intel"
#   arm64   -> "-AppleSilicon"
#   other   -> "-<machine>"
#
# Override:
#   export EBC_MACOS_RELEASE_ARCH_SUFFIX="-x86_64"
# Disable suffix:
#   export EBC_NO_MACOS_RELEASE_ARCH_SUFFIX=1

if [[ "${EBC_NO_MACOS_RELEASE_ARCH_SUFFIX:-}" == "1" ]]; then
  EBC_MACOS_RELEASE_ARCH_SUFFIX=""
elif [[ -n "${EBC_MACOS_RELEASE_ARCH_SUFFIX+x}" ]]; then
  true # keep caller's value (may be empty)
else
  _ebc_build_machine="$(/usr/bin/uname -m)"
  case "$_ebc_build_machine" in
    x86_64) EBC_MACOS_RELEASE_ARCH_SUFFIX="-Intel" ;;
    arm64) EBC_MACOS_RELEASE_ARCH_SUFFIX="-AppleSilicon" ;;
    *) EBC_MACOS_RELEASE_ARCH_SUFFIX="-${_ebc_build_machine}" ;;
  esac
fi
unset _ebc_build_machine 2>/dev/null || true
