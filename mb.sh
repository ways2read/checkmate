#!/usr/bin/env bash
# Mac Build: pull latest, then run the macOS release build
# (build, sign, notarize, OneDrive copy, Azure publish of dmg + version.json).
# Run from anywhere: ./mb.sh [marketing-version] [--skip-azure-publish]
set -e
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

git pull
./scripts/build_macos_release.sh "$@"
