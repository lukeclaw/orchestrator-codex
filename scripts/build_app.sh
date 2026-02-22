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

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== Orchestrator Build ==="
echo "Project root: $PROJECT_ROOT"
echo ""

# Step 1: Build the Python sidecar
echo "--- Step 1: Building sidecar ---"
if [[ "${1:-}" == "--skip-frontend" ]]; then
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
echo "=== Build complete ==="
echo "App bundle: src-tauri/target/release/bundle/macos/Orchestrator.app"
echo "DMG:        src-tauri/target/release/bundle/dmg/Orchestrator_*.dmg"
