#!/bin/bash
# Generate latest.json for the Tauri updater from build artifacts.
#
# Usage:
#   ./scripts/generate-latest-json.sh
#
# Looks for .app.tar.gz and .app.tar.gz.sig in the Tauri build output,
# reads the version from tauri.conf.json, and writes latest.json next
# to the other artifacts.
#
# Upload latest.json to the GitHub release alongside the DMG and tar.gz.

set -euo pipefail

PROJECT_ROOT="$(git rev-parse --show-toplevel)"
BUNDLE_DIR="$PROJECT_ROOT/src-tauri/target/release/bundle/macos"
GITHUB_REPO="yudongqiu/orchestrator"

# Read version from tauri.conf.json
VERSION=$(grep '"version"' "$PROJECT_ROOT/src-tauri/tauri.conf.json" | head -1 | sed 's/.*"version": "\(.*\)".*/\1/')

# Find the updater artifacts
TAR_GZ=$(find "$BUNDLE_DIR" -name "*.app.tar.gz" ! -name "*.sig" | head -1)
SIG_FILE="${TAR_GZ}.sig"

if [[ -z "$TAR_GZ" || ! -f "$TAR_GZ" ]]; then
    echo "Error: No .app.tar.gz found in $BUNDLE_DIR"
    echo "Did you build with TAURI_SIGNING_PRIVATE_KEY set?"
    exit 1
fi

if [[ ! -f "$SIG_FILE" ]]; then
    echo "Error: Signature file not found: $SIG_FILE"
    exit 1
fi

TAR_GZ_NAME=$(basename "$TAR_GZ")
SIGNATURE=$(cat "$SIG_FILE")
PUB_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
OUTPUT="$BUNDLE_DIR/latest.json"

cat > "$OUTPUT" <<EOF
{
  "version": "$VERSION",
  "pub_date": "$PUB_DATE",
  "platforms": {
    "darwin-aarch64": {
      "signature": "$SIGNATURE",
      "url": "https://github.com/$GITHUB_REPO/releases/download/v$VERSION/$TAR_GZ_NAME"
    },
    "darwin-x86_64": {
      "signature": "$SIGNATURE",
      "url": "https://github.com/$GITHUB_REPO/releases/download/v$VERSION/$TAR_GZ_NAME"
    }
  }
}
EOF

echo "Generated: $OUTPUT"
echo "  Version:  $VERSION"
echo "  Artifact: $TAR_GZ_NAME"
echo ""

# Collect all release files into gh_release/
RELEASE_DIR="$PROJECT_ROOT/src-tauri/target/release/bundle/gh_release"
rm -rf "$RELEASE_DIR"
mkdir -p "$RELEASE_DIR"

VERSIONED_DMG=$(find "$BUNDLE_DIR/../dmg" -name "Orchestrator_*.dmg" ! -name "Orchestrator_aarch64.dmg" | head -1)
cp "$VERSIONED_DMG"    "$RELEASE_DIR/Orchestrator_aarch64.dmg"
cp "$TAR_GZ"           "$RELEASE_DIR/"
cp "$SIG_FILE"         "$RELEASE_DIR/"
cp "$OUTPUT"           "$RELEASE_DIR/"

echo "Release files collected in: $RELEASE_DIR"
echo "  1. Orchestrator_aarch64.dmg  (stable download link)"
echo "  2. $(basename "$TAR_GZ")"
echo "  3. $(basename "$SIG_FILE")"
echo "  4. latest.json"
