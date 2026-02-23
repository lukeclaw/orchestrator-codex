#!/bin/bash

# 1. Configuration - USE THE EXACT NAME FROM: security find-identity -v -p codesigning
IDENTITY="Developer ID Application: Yudong Qiu (XT8BC8793B)"

# 2. Paths
INTERNAL_DIR="src-tauri/binaries/orchestrator-server-sidecar/_internal"
MAIN_BINARY="src-tauri/binaries/orchestrator-server-sidecar/orchestrator-server"

echo "Cleaning attributes and signing Python environment..."
xattr -rc "$INTERNAL_DIR"
find "$INTERNAL_DIR" -type f \( -name "*.so" -o -name "*.dylib" -o -perm +111 \) -print0 | xargs -0 codesign --force --options runtime --timestamp --sign "$IDENTITY"

echo "Signing main sidecar binary with Hardened Runtime..."
# Clear extended attributes first to avoid "resource fork" errors
xattr -rc "$MAIN_BINARY"

# Explicitly sign the main binary with the required flags
codesign --force --options runtime --timestamp --sign "$IDENTITY" "$MAIN_BINARY"

# Verify the signature locally before building
echo "Verifying signature..."
codesign -vvv --display --entitlements :- "$MAIN_BINARY"

echo "✅ Pre-sign complete."
