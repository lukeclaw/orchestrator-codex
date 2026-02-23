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
echo "✔ Release process complete."
