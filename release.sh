#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status
set -e

# Function to display usage
usage() {
    echo "Usage: $0 <new_version>"
    echo "Example: $0 v1.2.3"
    exit 1
}

# Check if version argument is provided
if [ -z "$1" ]; then
    usage
fi

NEW_VERSION=$1

# Validate the version format (vX.Y.Z)
if [[ ! "$NEW_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Error: Version must be in the format vX.Y.Z (e.g., v1.2.3)"
    exit 1
fi

# Update the version in pyproject.toml
echo "Updating version in pyproject.toml to $NEW_VERSION"
sed -i.bak "s/^version = \".*\"/version = \"$NEW_VERSION\"/" pyproject.toml
rm pyproject.toml.bak

# Commit the change
git add pyproject.toml
git commit -m "Bump version to $NEW_VERSION"

# Create a new git tag
git tag "$NEW_VERSION"

# Push commit and tag to GitHub
git push origin master
git push origin "$NEW_VERSION"

echo "Released version $NEW_VERSION successfully."
