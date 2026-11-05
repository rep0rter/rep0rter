#!/usr/bin/env bash
# Print the exact reviewer task: the unchanged brief plus the folder location.
#   scripts/design-review/reviewer-task.sh /tmp/design-review-blind/r01
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
cat "$root/docs/design-review/brief.md"
printf '\n---\n\nScreenshot folder: %s\n' "$1"
