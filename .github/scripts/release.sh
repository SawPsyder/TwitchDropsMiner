#!/usr/bin/env bash
set -euo pipefail

# release.sh - Cut a new release on this fork
# Usage: release.sh <version> [source_branch]
#
# This fork does NOT use upstream rangermix/TwitchDropsMiner's release pipeline
# (Docker Hub, Gemini-generated notes, release/<version> branches). Releases
# here are cut directly on `main` from a plain `v<version>` tag. Pushing that
# tag triggers two independent GitHub Actions:
#   - ghcr-publish.yml   builds and publishes versioned GHCR images
#                        (ghcr.io/<owner>/twitchdropsminer:<version>, :<major.minor>, :<major>, :latest)
#   - github-release.yml creates the GitHub Release from RELEASE_NOTES.md
#
# Prerequisite: RELEASE_NOTES.md must already contain a
# "# Release Notes - v<version>" section, committed, before running this.
#
# Steps performed:
#   1. Fast-forward `main` to <source_branch> (default: develop)
#   2. Validate the requested version against the current one
#   3. Require the RELEASE_NOTES.md entry to already exist
#   4. Bump src/version.py and pyproject.toml, commit, push
#   5. Tag v<version> and push the tag
#
# Requirements: clean working tree, push access to origin.

usage() {
    echo "Usage: $0 <version> [source_branch]"
    echo ""
    echo "Examples:"
    echo "  $0 1.3.0            # release develop's tip as 1.3.0"
    echo "  $0 1.3.1 main       # release directly from main (e.g. a hotfix), no merge"
    exit 1
}

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    usage
fi

VERSION="$1"
SOURCE_BRANCH="${2:-develop}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

if [ -n "$(git status --porcelain)" ]; then
    echo -e "${RED}Error: working tree is not clean. Commit or stash changes first.${NC}" >&2
    exit 1
fi

echo -e "${YELLOW}Fetching origin...${NC}"
git fetch origin

echo -e "${YELLOW}Checking out main...${NC}"
git checkout main
git merge --ff-only origin/main

if [ "$SOURCE_BRANCH" != "main" ]; then
    echo -e "${YELLOW}Fast-forwarding main to origin/$SOURCE_BRANCH...${NC}"
    if ! git merge --ff-only "origin/$SOURCE_BRANCH"; then
        echo -e "${RED}Error: main is not a fast-forward ancestor of origin/$SOURCE_BRANCH.${NC}" >&2
        echo -e "${RED}Resolve manually (rebase/merge) and re-run.${NC}" >&2
        exit 1
    fi
fi

echo -e "${YELLOW}Validating version format...${NC}"
"$SCRIPT_DIR/validate_semver.sh" "$VERSION"

echo -e "${YELLOW}Reading current version...${NC}"
VERSION_OUTPUT_FILE="$(mktemp)"
trap 'rm -f "$VERSION_OUTPUT_FILE"' EXIT
GITHUB_OUTPUT="$VERSION_OUTPUT_FILE" "$SCRIPT_DIR/extract_version.sh"
CURRENT_VERSION="$(grep '^version=' "$VERSION_OUTPUT_FILE" | cut -d= -f2)"

echo -e "${YELLOW}Validating $VERSION > $CURRENT_VERSION...${NC}"
"$SCRIPT_DIR/validate_semver.sh" "$VERSION" ">$CURRENT_VERSION"

echo -e "${YELLOW}Checking for RELEASE_NOTES.md entry...${NC}"
if ! grep -q "^# Release Notes - v$VERSION\$" RELEASE_NOTES.md; then
    echo -e "${RED}Error: RELEASE_NOTES.md has no '# Release Notes - v$VERSION' section.${NC}" >&2
    echo -e "${RED}Draft the release notes for this version first, commit them, then re-run.${NC}" >&2
    exit 1
fi

TAG_NAME="v$VERSION"
if git rev-parse "$TAG_NAME" &>/dev/null || git ls-remote --tags origin | grep -q "refs/tags/$TAG_NAME\$"; then
    echo -e "${RED}Error: tag $TAG_NAME already exists.${NC}" >&2
    exit 1
fi

echo -e "${YELLOW}Bumping version to $VERSION...${NC}"
echo "__version__ = \"$VERSION\"" > src/version.py
sed -i "s/^version = \"[^\"]*\"\(.*\)/version = \"$VERSION\"\1/" pyproject.toml

git add src/version.py pyproject.toml
git commit -m "chore: bump version to $VERSION"
git push origin main

echo -e "${YELLOW}Tagging $TAG_NAME...${NC}"
git tag "$TAG_NAME"
git push origin "$TAG_NAME"

echo ""
echo -e "${GREEN}✅ Release $VERSION cut.${NC}"
echo "GitHub Actions will now:"
echo "  - build and publish ghcr.io/<owner>/twitchdropsminer:$VERSION (+ :<major.minor>, :<major>, :latest)"
echo "  - create the GitHub Release from RELEASE_NOTES.md"
echo ""
echo "Check progress: gh run list --workflow ghcr-publish.yml"
echo "                gh run list --workflow github-release.yml"
