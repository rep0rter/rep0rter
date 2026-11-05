#!/usr/bin/env bash
# Print the exact reviewer task: the unchanged brief plus the folder location.
#   scripts/design-review/reviewer-task.sh /tmp/design-review-blind/r01 [docs/design-review/brief-v2.md]
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
cat "$root/${2:-docs/design-review/brief.md}"
printf '\n---\n\nScreenshot folder: %s\n' "$1"
