#!/usr/bin/env bash
# Seed fresh fictional previews for one checkout, serve them locally, capture
# every state, then stop the servers.
#   scripts/design-review/round.sh <checkout> <first-port> <output-dir>
# Uses ports <first-port> (site), +1 (empty site), +2 (login disabled).
set -euo pipefail
checkout=$(cd "$1" && pwd); port=$2; out=$3
tools=$(cd "$(dirname "$0")" && pwd)
python=${PYTHON:-$tools/../../.venv/bin/python}
work=$(mktemp -d /tmp/design-review-XXXXXX)
export PYTHONPATH=$checkout
"$python" "$tools/preview.py" seed --data "$work/site" --port "$port"
"$python" "$tools/preview.py" seed-empty --data "$work/empty" --port "$((port + 1))"
pids=()
"$python" "$tools/preview.py" serve --data "$work/site" --port "$port" >"$work/site.log" 2>&1 & pids+=($!)
"$python" "$tools/preview.py" serve --data "$work/empty" --port "$((port + 1))" >"$work/empty.log" 2>&1 & pids+=($!)
PREVIEW_LOGIN_DISABLED=1 "$python" "$tools/preview.py" serve --data "$work/site" --port "$((port + 2))" >"$work/disabled.log" 2>&1 & pids+=($!)
trap 'kill "${pids[@]}" 2>/dev/null || true' EXIT
for p in "$port" "$((port + 1))" "$((port + 2))"; do
  for _ in $(seq 50); do curl -fs "http://127.0.0.1:$p/healthz" >/dev/null && break; sleep 0.2; done
done
"$python" "$tools/capture.py" --base "http://127.0.0.1:$port" --empty "http://127.0.0.1:$((port + 1))" \
  --disabled "http://127.0.0.1:$((port + 2))" --out "$out" ${ONLY:+--only $ONLY} ${REDESIGN:+--redesign}
echo "preview data: $work"
