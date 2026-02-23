#!/bin/bash
# Bump the version across all config files.
#
# Usage:
#   ./scripts/bump-version.sh 0.2.0
#
# This updates:
#   pyproject.toml          (single source of truth)
#   src-tauri/tauri.conf.json
#   src-tauri/Cargo.toml
#   frontend/package.json
#
# Python code reads the version from pyproject.toml at runtime
# via importlib.metadata, so no Python files need updating.

set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: $0 <new-version>"
    echo "Example: $0 0.2.0"
    exit 1
fi

NEW_VERSION="$1"

# Validate semver-ish format
if ! echo "$NEW_VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    echo "Error: version must be in X.Y.Z format (got: $NEW_VERSION)"
    exit 1
fi

PROJECT_ROOT="$(git rev-parse --show-toplevel)"

# Semver pattern to match any X.Y.Z version
V='[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*'

# Read current version from pyproject.toml (for display only)
CURRENT=$(grep '^version = ' "$PROJECT_ROOT/pyproject.toml" | head -1 | sed 's/version = "\(.*\)"/\1/')
echo "Bumping version: $CURRENT → $NEW_VERSION"

# 1. pyproject.toml — match first 'version = "X.Y.Z"' line
sed -i '' "s/^version = \"$V\"/version = \"$NEW_VERSION\"/" "$PROJECT_ROOT/pyproject.toml"

# 2. src-tauri/tauri.conf.json — match "version": "X.Y.Z"
sed -i '' "s/\"version\": \"$V\"/\"version\": \"$NEW_VERSION\"/" "$PROJECT_ROOT/src-tauri/tauri.conf.json"

# 3. src-tauri/Cargo.toml — match first 'version = "X.Y.Z"' line
sed -i '' "s/^version = \"$V\"/version = \"$NEW_VERSION\"/" "$PROJECT_ROOT/src-tauri/Cargo.toml"

# 4. frontend/package.json — match "version": "X.Y.Z"
sed -i '' "s/\"version\": \"$V\"/\"version\": \"$NEW_VERSION\"/" "$PROJECT_ROOT/frontend/package.json"
# Update the root entry in package-lock.json
sed -i '' "s/\"version\": \"$V\"/\"version\": \"$NEW_VERSION\"/" "$PROJECT_ROOT/frontend/package-lock.json"

echo ""
echo "Updated files:"
grep -n "\"$NEW_VERSION\"\|= \"$NEW_VERSION\"" \
    "$PROJECT_ROOT/pyproject.toml" \
    "$PROJECT_ROOT/src-tauri/tauri.conf.json" \
    "$PROJECT_ROOT/src-tauri/Cargo.toml" \
    "$PROJECT_ROOT/frontend/package.json"

echo ""
echo "Done. To release:"
echo "  git add -A && git commit -m \"chore: bump version to $NEW_VERSION\""
echo "  git tag v$NEW_VERSION"
echo "  git push && git push --tags"
