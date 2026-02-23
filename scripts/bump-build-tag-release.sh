#!/bin/bash

# Usage: ./scripts/bump-build-tag.sh <version>
# Example: ./scripts/bump-build-tag.sh 0.2.0

set -e


VERSION="$1"

if [ -z "$VERSION" ]; then
  echo "No version specified. Attempting to auto-increment the latest tag..."
  LATEST_TAG=$(git tag --list 'v*' --sort=-v:refname | head -n1)
  if [ -z "$LATEST_TAG" ]; then
    echo "Error: No existing version tag found. Please specify a version manually."
    echo "Usage: $0 <version>"
    exit 1
  fi
  # Remove leading 'v' if present
  LATEST_VERSION=${LATEST_TAG#v}
  IFS='.' read -r MAJOR MINOR PATCH <<< "$LATEST_VERSION"
  if [[ -z "$MAJOR" || -z "$MINOR" || -z "$PATCH" ]]; then
    echo "Error: Latest tag '$LATEST_TAG' is not in semantic version format (vX.Y.Z)."
    exit 1
  fi
  PATCH=$((PATCH + 1))
  VERSION="$MAJOR.$MINOR.$PATCH"
  echo "Auto-incremented version: $VERSION"
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "⚠️  Uncommitted changes detected! Please commit or stash all changes before running this script."
  exit 1
fi

echo "==> Repository is clean. Proceeding..."

echo "==> Bumping version to $VERSION..."
./scripts/bump-version.sh "$VERSION"
echo "✔ Version bumped."

echo "==> Building app..."
./scripts/build_app.sh
echo "✔ App build complete."

echo "==> Generating latest.json..."
./scripts/generate-latest-json.sh
echo "✔ latest.json generated."

echo "==> Adding all changes to git..."
git add -A
echo "==> Committing changes..."
git commit -m "chore: bump version to $VERSION"
echo "==> Tagging version v$VERSION..."
git tag "v$VERSION"
echo "==> Pushing to remote..."
git push
git push --tags

# Create GitHub release using gh CLI if available, and upload release files
if command -v gh >/dev/null 2>&1; then
  echo "==> Creating GitHub release via gh CLI and uploading artifacts..."
  RELEASE_DIR="src-tauri/target/release/bundle/gh_release"
  DMG_FILE=$(ls "$RELEASE_DIR"/Orchestrator_*.dmg 2>/dev/null | head -n1)
  TAR_FILE=$(ls "$RELEASE_DIR"/Orchestrator.app.tar.gz 2>/dev/null | head -n1)
  SIG_FILE=$(ls "$RELEASE_DIR"/Orchestrator.app.tar.gz.sig 2>/dev/null | head -n1)
  LATEST_JSON=$(ls "$RELEASE_DIR"/latest.json 2>/dev/null | head -n1)

  if [[ -f "$DMG_FILE" && -f "$TAR_FILE" && -f "$SIG_FILE" && -f "$LATEST_JSON" ]]; then
    gh release create "v$VERSION" \
      --title "Orchestrator v$VERSION" \
      --notes "Automated release for version $VERSION" \
      --target main \
      "$DMG_FILE" "$TAR_FILE" "$SIG_FILE" "$LATEST_JSON"
    echo "✔ GitHub release created and files uploaded."
  else
    echo "Warning: One or more release files not found in $RELEASE_DIR. Creating release without files."
    gh release create "v$VERSION" \
      --title "Orchestrator v$VERSION" \
      --notes "Automated release for version $VERSION" \
      --target main
    echo "✔ GitHub release created (no files uploaded)."
  fi
else
  echo "gh CLI not found, skipping GitHub release creation."
fi

echo "✔ Release process complete."
