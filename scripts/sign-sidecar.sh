#!/bin/bash

# 1. Configuration - Replace with your 10-character Team ID or Full Identity Name
# You can find this by running: security find-identity -v -p codesigning
IDENTITY="Developer ID Application: Yudong Qiu (XT8BC8793B)"

# 2. Path to your sidecar's internal files
# Based on your error, it's: src-tauri/binaries/orchestrator-server-sidecar/_internal
TARGET_DIR="src-tauri/binaries/orchestrator-server-sidecar/_internal"

echo "Starting manual signing for Python sidecar binaries..."

# 3. Clean up any metadata that can break codesigning
xattr -rc "$TARGET_DIR"

# 4. Recursively sign all .so, .dylib, and executable files
# We use --options runtime for the Hardened Runtime (required for notarization)
find "$TARGET_DIR" -type f \( -name "*.so" -o -name "*.dylib" -o -perm +111 \) -print0 | xargs -0 codesign --force --options runtime --timestamp --sign "$IDENTITY"

# 5. Sign the main sidecar executables themselves
# Replace 'aarch64-apple-darwin' with your specific targets (or use a wildcard)
codesign --force --options runtime --timestamp --sign "$IDENTITY" src-tauri/binaries/orchestrator-server-sidecar-*

echo "✅ Sidecar signing complete."
