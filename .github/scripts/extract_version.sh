#!/usr/bin/env bash
set -euo pipefail

# Script: extract_version.sh
# Description: Validates that src/version.py and pyproject.toml agree, and
#              outputs the current version.
# Usage: extract_version.sh

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

if [ ! -f "src/version.py" ]; then
    echo -e "${RED}Error: src/version.py not found${NC}"
    exit 1
fi

VERSION_PY=$(grep -oP '__version__ = "\K[^"]+' src/version.py)
echo "src/version.py version: $VERSION_PY"

if [ ! -f "pyproject.toml" ]; then
    echo -e "${RED}Error: pyproject.toml not found${NC}"
    exit 1
fi

VERSION_TOML=$(grep -oP '^version = "\K[^"]+' pyproject.toml)
echo "pyproject.toml version: $VERSION_TOML"

if [ "$VERSION_PY" != "$VERSION_TOML" ]; then
    echo -e "${RED}Error: Version mismatch between files:${NC}"
    echo -e "${RED}  src/version.py:   $VERSION_PY${NC}"
    echo -e "${RED}  pyproject.toml:   $VERSION_TOML${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Version files match: $VERSION_PY${NC}"
VERSION="$VERSION_PY"

# Output to GITHUB_OUTPUT if available
if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "version=$VERSION" >> "$GITHUB_OUTPUT"
    if echo "$VERSION" | grep -q '-'; then
        echo "is_prerelease=true" >> "$GITHUB_OUTPUT"
    else
        echo "is_prerelease=false" >> "$GITHUB_OUTPUT"
    fi
fi

exit 0
