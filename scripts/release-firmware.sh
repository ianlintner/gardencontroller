#!/usr/bin/env bash
# Bump FIRMWARE_VERSION in config.h, commit, tag, and push.
# CI picks up the v* tag and builds + publishes the GitHub Release.
#
# Usage: ./scripts/release-firmware.sh 1.2.0
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="$ROOT/firmware/garden-node/config.h"
VERSION="${1:-}"

if [[ -z "$VERSION" ]] || ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Usage: $0 <major.minor.patch>   e.g.  $0 1.2.0" >&2
  exit 1
fi

# Check working tree is clean
if ! git -C "$ROOT" diff --quiet HEAD; then
  echo "error: working tree has uncommitted changes — commit or stash first" >&2
  exit 1
fi

echo "Bumping FIRMWARE_VERSION to $VERSION in config.h..."
sed -i '' "s/#define FIRMWARE_VERSION *\"[^\"]*\"/#define FIRMWARE_VERSION \"$VERSION\"/" "$CONFIG"
grep "FIRMWARE_VERSION" "$CONFIG"

git -C "$ROOT" add "$CONFIG"
git -C "$ROOT" commit -m "chore(firmware): bump version to $VERSION"
git -C "$ROOT" tag "v$VERSION"
git -C "$ROOT" push origin main "v$VERSION"
echo "Tagged v$VERSION and pushed. CI will build + publish the GitHub Release."
