#!/bin/bash
# Build the full Orchestrator macOS app (sidecar + Tauri shell).
#
# Prerequisites:
#   - Rust toolchain (rustup)
#   - Node.js + npm
#   - Python 3.11+ with uv
#   - Tauri CLI: cargo install tauri-cli
#
# Usage:
#   ./scripts/build_app.sh          # Full build
#   ./scripts/build_app.sh --skip-frontend  # Skip npm build (if frontend unchanged)
#   ./scripts/build_app.sh --pkg-only       # Only run Step 3 (create signed .pkg)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== Orchestrator Build ==="
echo "Project root: $PROJECT_ROOT"
echo ""

ARG="${1:-}"

if [[ "$ARG" == "--pkg-only" ]]; then
    echo "(Skipping Steps 1, 1a, 2 — jumping to Step 3)"
    echo ""
    cd "$PROJECT_ROOT"

    APP_SRC="src-tauri/target/release/bundle/macos/Orchestrator.app"
    APP_STORE_APP="src-tauri/target/release/bundle/macos/Orchestrator-AppStore.app"
    PKG_OUTPUT="src-tauri/target/release/bundle/pkg/Orchestrator.pkg"
    APP_STORE_IDENTITY="Apple Distribution: Yudong Qiu (XT8BC8793B)"
    PKG_INSTALLER_IDENTITY="3rd Party Mac Developer Installer: Yudong Qiu (XT8BC8793B)"

    mkdir -p "$(dirname "$PKG_OUTPUT")"
    rm -rf "$APP_STORE_APP"
    cp -R "$APP_SRC" "$APP_STORE_APP"
    codesign --force --deep --options runtime --timestamp \
             --sign "$APP_STORE_IDENTITY" \
             --entitlements "src-tauri/Entitlements.plist" \
             "$APP_STORE_APP"
    productbuild --sign "$PKG_INSTALLER_IDENTITY" \
                 --component "$APP_STORE_APP" /Applications \
                 "$PKG_OUTPUT"

    echo ""
    echo "=== PKG build complete ==="
    echo "PKG (App Store): $PKG_OUTPUT"
    exit 0
fi

# Step 1: Build the Python sidecar
echo "--- Step 1: Building sidecar ---"
if [[ "$ARG" == "--skip-frontend" ]]; then
    echo "(Skipping frontend build)"
    # Just build PyInstaller part
    cd "$PROJECT_ROOT"
    uv run --extra build python -m PyInstaller orchestrator.spec --clean --noconfirm
    # Copy to tauri binaries
    uv run --extra build python scripts/build_sidecar.py 2>/dev/null || {
        # If the full script fails, just do the copy
        TRIPLE=$(uv run python -c "from scripts.build_sidecar import get_target_triple; print(get_target_triple())")
        cp dist/orchestrator-server "src-tauri/binaries/orchestrator-server-$TRIPLE"
        chmod +x "src-tauri/binaries/orchestrator-server-$TRIPLE"
    }
else
    uv run --extra build python scripts/build_sidecar.py
fi
echo ""

# Step 1a: sign the side car app
echo "--- Step 1a: Signing sidecar app ---"
cd "$PROJECT_ROOT"
bash ./scripts/sign-sidecar.sh


# Step 2: Build the Tauri app
echo "--- Step 2: Building Tauri app ---"
cd "$PROJECT_ROOT"

# Check for Tauri CLI
if ! command -v cargo-tauri &>/dev/null; then
    if ! cargo tauri --version &>/dev/null 2>&1; then
        echo "Error: Tauri CLI not found. Install with: cargo install tauri-cli"
        exit 1
    fi
fi

cargo tauri build

echo ""

# Step 3: Create signed .pkg for Mac App Store upload
echo "--- Step 3: Creating signed .pkg for App Store ---"
cd "$PROJECT_ROOT"

APP_SRC="src-tauri/target/release/bundle/macos/Orchestrator.app"
APP_STORE_APP="src-tauri/target/release/bundle/macos/Orchestrator-AppStore.app"
PKG_OUTPUT="src-tauri/target/release/bundle/pkg/Orchestrator.pkg"
APP_STORE_IDENTITY="Apple Distribution: Yudong Qiu (XT8BC8793B)"
PKG_INSTALLER_IDENTITY="Mac Installer Distribution: Yudong Qiu (XT8BC8793B)"

mkdir -p "$(dirname "$PKG_OUTPUT")"

# The App Store .app must be signed with Apple Distribution (not Developer ID Application).
# Copy the app and re-sign it for App Store submission.
rm -rf "$APP_STORE_APP"
cp -R "$APP_SRC" "$APP_STORE_APP"
codesign --force --deep --options runtime --timestamp \
         --sign "$APP_STORE_IDENTITY" \
         --entitlements "src-tauri/Entitlements.plist" \
         "$APP_STORE_APP"

productbuild --sign "$PKG_INSTALLER_IDENTITY" \
             --component "$APP_STORE_APP" /Applications \
             "$PKG_OUTPUT"

echo ""
echo "=== Build complete ==="
echo "App bundle: src-tauri/target/release/bundle/macos/Orchestrator.app"
echo "DMG:        src-tauri/target/release/bundle/dmg/Orchestrator_*.dmg"
echo "PKG (App Store): $PKG_OUTPUT"
