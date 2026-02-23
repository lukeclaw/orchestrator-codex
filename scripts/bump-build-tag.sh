#!/bin/bash

# Usage: ./scripts/bump-build-tag.sh <version>
# Example: ./scripts/bump-build-tag.sh 0.2.0

set -e

VERSION="$1"

if [ -z "$VERSION" ]; then
  echo "Error: No version specified."
  echo "Usage: $0 <version>"
  exit 1
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
