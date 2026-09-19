#!/bin/sh
# Upload the generated site to Workers Static Assets.
#
#   REP0RTER_DATA_DIR=/data deploy/cloudflare/publish.sh
#
# Step 1 of the serverless migration: the site is still built on the host, and
# /auth/*, /submit and /projects* still reach the Flask app through APP_ORIGIN.
# Once the pipeline moves to GitHub Actions this runs there instead.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
site="${REP0RTER_DATA_DIR:-./data}/site"

[ -d "$site" ] || { echo "no site at $site; run 'python -m rep0rter build-site' first" >&2; exit 1; }
[ -f "$site/index.html" ] || { echo "$site has no index.html; refusing to publish" >&2; exit 1; }

# _headers must sit inside the uploaded directory, but the builder stays
# host-agnostic, so it is copied in here rather than emitted by site.py.
cp "$here/_headers" "$site/_headers"

exec npx wrangler deploy --config "$here/wrangler.jsonc" --assets "$site"
