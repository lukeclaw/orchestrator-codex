#!/bin/bash
# Build the full Orchestrator macOS app (sidecar + Tauri shell).
#
# Prerequisites:
#   - Rust toolchain (rustup)
#   - Node.js + npm
#   - Python 3.11+ with uv
#   - Tauri CLI: cargo install tauri-cli
#   - create-dmg: brew install create-dmg
#
# Usage:
#   ./scripts/build_app.sh                       # Build both (DMG + App Store PKG)
#   ./scripts/build_app.sh --dmg-only            # DMG only (Developer ID direct distribution)
#   ./scripts/build_app.sh --pkg-only            # App Store PKG only (reuses existing .app)
#   ./scripts/build_app.sh --skip-frontend       # Skip npm build (if frontend unchanged)
#   ./scripts/build_app.sh --dmg-only --skip-frontend

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# --- Signing identities ---
DEV_ID_IDENTITY="Developer ID Application: Yudong Qiu (XT8BC8793B)"
APP_STORE_IDENTITY="Apple Distribution: Yudong Qiu (XT8BC8793B)"
PKG_INSTALLER_IDENTITY="3rd Party Mac Developer Installer: Yudong Qiu (XT8BC8793B)"

# --- Output paths ---
APP_BUNDLE="src-tauri/target/release/bundle/macos/Orchestrator.app"
APP_STORE_BUNDLE="src-tauri/target/release/bundle/macos/Orchestrator-AppStore.app"
PKG_OUTPUT="src-tauri/target/release/bundle/pkg/Orchestrator.pkg"

# --- Argument parsing ---
BUILD_DMG=true
BUILD_PKG=true
SKIP_FRONTEND=false

for arg in "$@"; do
    case $arg in
        --dmg-only)      BUILD_PKG=false ;;
        --pkg-only)      BUILD_DMG=false ;;
        --skip-frontend) SKIP_FRONTEND=true ;;
        *) echo "Unknown argument: $arg"; exit 1 ;;
    esac
done

echo "=== Orchestrator Build ==="
echo "Project root: $PROJECT_ROOT"
[[ "$BUILD_DMG" == "true" ]] && echo "  • DMG  (Developer ID — direct distribution)"
[[ "$BUILD_PKG" == "true" ]] && echo "  • PKG  (App Store)"
echo ""

cd "$PROJECT_ROOT"

# ============================================================
# Pipeline A: DMG (Developer ID)
# Sidecar signed with Developer ID → cargo tauri build → DMG
# ============================================================
if [[ "$BUILD_DMG" == "true" ]]; then
    echo "--- [DMG] Step 1: Building sidecar ---"
    if [[ "$SKIP_FRONTEND" == "true" ]]; then
        echo "(Skipping frontend build)"
        uv run --extra build python -m PyInstaller orchestrator.spec --clean --noconfirm
        uv run --extra build python scripts/build_sidecar.py 2>/dev/null || {
            TRIPLE=$(uv run python -c "from scripts.build_sidecar import get_target_triple; print(get_target_triple())")
            cp dist/orchestrator-server "src-tauri/binaries/orchestrator-server-$TRIPLE"
            chmod +x "src-tauri/binaries/orchestrator-server-$TRIPLE"
        }
    else
        uv run --extra build python scripts/build_sidecar.py
    fi
    echo ""

    echo "--- [DMG] Step 1a: Signing sidecar (Developer ID) ---"
    bash ./scripts/sign-sidecar.sh
    echo ""

    echo "--- [DMG] Step 2: Building Tauri app ---"
    if ! command -v cargo-tauri &>/dev/null; then
        if ! cargo tauri --version &>/dev/null 2>&1; then
            echo "Error: Tauri CLI not found. Install with: cargo install tauri-cli"
            exit 1
        fi
    fi
    cargo tauri build
    echo ""

    echo "--- [DMG] Step 3: Copying DMG to stable name ---"
    DMG_DIR="src-tauri/target/release/bundle/dmg"
    VERSIONED_DMG=$(find "$DMG_DIR" -name "Orchestrator_*.dmg" | head -1)
    STABLE_DMG="$DMG_DIR/Orchestrator_aarch64.dmg"
    cp "$VERSIONED_DMG" "$STABLE_DMG"
    echo "  Stable DMG: $STABLE_DMG"
    echo ""
fi

# ============================================================
# Pipeline B: App Store PKG (Apple Distribution)
# Copy DMG app bundle, re-sign inside-out with Apple Distribution → productbuild
# If --pkg-only, reuses an existing .app from a previous DMG build.
# ============================================================
if [[ "$BUILD_PKG" == "true" ]]; then
    echo "--- [PKG] Step 3: Creating signed .pkg for App Store ---"

    if [[ ! -d "$APP_BUNDLE" ]]; then
        echo "Error: $APP_BUNDLE not found."
        echo "Run without --pkg-only first to produce the app bundle."
        exit 1
    fi

    mkdir -p "$(dirname "$PKG_OUTPUT")"
    rm -rf "$APP_STORE_BUNDLE"
    cp -R "$APP_BUNDLE" "$APP_STORE_BUNDLE"

    # Embed the App Store provisioning profile (required for sandboxed apps).
    echo "  Embedding provisioning profile..."
    cp "src-tauri/embedded.provisionprofile" \
       "$APP_STORE_BUNDLE/Contents/embedded.provisionprofile"

    # Sign inside-out: deepest binaries first, then the bundle.
    # --deep is unreliable for nested .so files, so we do it explicitly.
    # All executables must carry the app-sandbox entitlement for App Store validation.
    # Sidecar helpers use 'inherit' so they run within the parent app's sandbox.
    echo "  Re-signing nested libraries (Apple Distribution)..."
    find "$APP_STORE_BUNDLE" -type f \( -name "*.so" -o -name "*.dylib" \) -print0 | \
        xargs -0 codesign --force --options runtime --timestamp \
                          --sign "$APP_STORE_IDENTITY" \
                          --entitlements "src-tauri/Entitlements-sidecar.plist"

    echo "  Re-signing sidecar executables (Apple Distribution)..."
    find "$APP_STORE_BUNDLE/Contents/Resources" -type f -perm +111 \
         ! -name "*.so" ! -name "*.dylib" -print0 | \
        xargs -0 codesign --force --options runtime --timestamp \
                          --sign "$APP_STORE_IDENTITY" \
                          --entitlements "src-tauri/Entitlements-sidecar.plist"

    echo "  Re-signing app bundle (Apple Distribution)..."
    codesign --force --options runtime --timestamp \
             --sign "$APP_STORE_IDENTITY" \
             --entitlements "src-tauri/Entitlements-appstore.plist" \
             "$APP_STORE_BUNDLE"

    echo "  Running productbuild..."
    productbuild --sign "$PKG_INSTALLER_IDENTITY" \
                 --component "$APP_STORE_BUNDLE" /Applications \
                 "$PKG_OUTPUT"
    echo ""
fi

# ============================================================
echo "=== Build complete ==="
[[ "$BUILD_DMG" == "true" ]] && echo "DMG:             src-tauri/target/release/bundle/dmg/Orchestrator_*.dmg"
[[ "$BUILD_DMG" == "true" ]] && echo "Stable DMG:      src-tauri/target/release/bundle/dmg/Orchestrator_aarch64.dmg"
[[ "$BUILD_PKG" == "true" ]] && echo "PKG (App Store): $PKG_OUTPUT"
