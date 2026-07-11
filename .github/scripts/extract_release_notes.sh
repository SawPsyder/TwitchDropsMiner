#!/bin/bash
set -e

# Script to extract release notes for a specific version from RELEASE_NOTES.md
# Usage: ./extract_release_notes.sh <version>
#
# Writes to extracted_release_notes.md (NOT release_notes.md - on a
# case-insensitive filesystem that name collides with RELEASE_NOTES.md and
# will silently truncate it).

VERSION="$1"
OUTPUT_FILE="extracted_release_notes.md"

if [ -z "$VERSION" ]; then
  echo "❌ Error: Version argument required"
  echo "Usage: $0 <version>"
  exit 1
fi

echo "Extracting release notes for version $VERSION from RELEASE_NOTES.md"

# Extract the section for the current version
# Find the line with "# Release Notes - vX.X.X" and extract until the next version or EOF
awk -v ver="$VERSION" '
  BEGIN { found=0; printing=0 }
  /^# Release Notes - v/ {
    if ($0 ~ ver) {
      found=1
      printing=1
      next
    } else if (found && printing) {
      exit
    }
  }
  printing { print }
' RELEASE_NOTES.md > "$OUTPUT_FILE"

# Check if we found content (should always succeed now)
if [ ! -s "$OUTPUT_FILE" ]; then
  echo "❌ Error: Could not extract release notes for version $VERSION"
  exit 1
fi

echo "✅ Successfully extracted release notes for version $VERSION"

# Figure out this fork's own GHCR image (ghcr.io/<owner>/twitchdropsminer),
# not upstream's Docker Hub image - this fork doesn't publish there.
OWNER="${GITHUB_REPOSITORY_OWNER:-}"
if [ -z "$OWNER" ]; then
  REMOTE_URL=$(git config --get remote.origin.url || echo "")
  OWNER=$(echo "$REMOTE_URL" | sed -E 's#.*[:/]([^/]+)/[^/]+\.git#\1#')
fi
OWNER=$(echo "$OWNER" | tr '[:upper:]' '[:lower:]')

# Append Docker information
{
  echo "---"
  echo ""
  echo "### Docker Images"
  echo ""
  echo '```bash'
  echo "docker pull ghcr.io/$OWNER/twitchdropsminer:$VERSION"
  echo '```'
} >> "$OUTPUT_FILE"

echo "✅ Release notes written to $OUTPUT_FILE"
