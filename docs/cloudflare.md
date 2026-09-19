# Native Cloudflare Workers deployment

The Cloudflare target runs the existing Python application in Python Workers,
with a separate JavaScript Worker serving public pages.
It does not use Containers, Docker, a Cloudflare Tunnel, or the Steam host at
runtime. Flask handles Google login and submissions, Browser Run renders cards
and browser-only feeds, and a Cron Trigger asks a singleton Durable Object to
run the hourly collector/editor/publisher cycle.

## Storage and concurrency

The singleton `Reporter` Durable Object serializes application writers. The
Python SQLite file is an ephemeral working copy. Every committed transaction
synchronously saves its changed 4 KiB pages in one Durable Object SQLite
transaction before returning to the caller. This preserves existing transaction
and Telegram outbox semantics. If persistence fails, the invocation aborts and
its local working copy is discarded before the next application operation.

Generated files are copied through a private service binding to the JavaScript
Worker and published to its `PublishedSite` Durable Object in a single transaction;
removed paths disappear at that same boundary. Website reads do not wait for a
collector or model request. Neither the database nor private account/session
records are available through the static file route. Local file locks are
replaced by the singleton's application mutex in Workers only.

This deployment is sized for the current small publication. The database working
copy is capped at 32 MiB and individual generated assets at 1.9 MB. A larger
publication needs storage and memory profiling before raising those caps. Keep
Cloudflare's Durable Object storage limits, Browser Run quota, and CPU limits in
view. Browser failures fail the build instead of silently producing broken cards.

## Build and test

Install `uv`, Node.js, and the pinned Python Workers tools:

```sh
uv tool install workers-py==1.17.3
python3 cloudflare/prepare.py
cd cloudflare
pywrangler dev
# Update an existing preview (see bootstrap ordering below for first deployment):
pywrangler deploy
npx wrangler@4.135.0 deploy --config wrangler-web.jsonc
```

`prepare.py` copies only application source and checked-in assets into the build
folder. It never copies `.env`, runtime data, deployment state, or Docker files.
`pylock.toml` locks the Python dependencies. Run the repository's offline tests
before deployment; `tests/test_worker_runtime.py` checks commit recovery,
rollback, persistence failure, and WAL snapshot recovery.

## Configuration and migration

Store application configuration as the JSON object in the Worker secret
`REP0RTER_CONFIG`. Its keys are the existing `REP0RTER_*`, `AI_*`, and `TELEGRAM_*`
variables. Upload it through Wrangler's stdin; never put values in command
arguments, source, logs, or git. `REP0RTER_SITE_URL` is the public origin configured
in Wrangler. Google OAuth continues using the same callback URL.

The Python engine and public frontend each have separate Wrangler configs.
The preview config defaults to `RUN_ENABLED=false`. The separate
`wrangler-production.jsonc` enables engine scheduling;
`wrangler-web-production.jsonc` attaches the public route. This prevents preview
and pre-cutover deployments from publishing duplicate Telegram messages. Use
preview credentials without a Telegram token. A temporary `MIGRATION_TOKEN`
secret protects the import and validation endpoints. The local admin client
reads this from an ignored, mode-0600 `.dev.vars` file.

```sh
python3 cloudflare/export.py --data-dir /path/to/readable/data --output /private/snapshot.zip
python3 cloudflare/manage.py import --url https://WORKER.workers.dev --archive /private/snapshot.zip
python3 cloudflare/manage.py check --url https://WORKER.workers.dev
python3 cloudflare/manage.py collect --url https://PREVIEW.workers.dev
python3 cloudflare/manage.py build --url https://PREVIEW.workers.dev
```

Export uses SQLite's online backup API, checks integrity, and archives one
published site generation plus the exclusions ledger and image cache. The
archive includes private account/session data: store it privately and never
commit it. Import validates the database and paths, and is disabled after the
first successful import or after scheduling is enabled. Never use an initial
preview snapshot as the final cutover snapshot while local writers are running.

## Subsequent code deployments

Push the commit to `main`, wait for its passing **Offline tests** push run, then
run `python3 cloudflare/deploy.py --production` from a clean checkout of that
commit. This verifies GitHub tests and remote main before deploying. Wrangler
uses its saved OAuth login or `CLOUDFLARE_API_TOKEN`; no Steam service is involved.
Automatic GitHub deployment requires a separately provisioned deployment API
token. The old Steam deployment timer must remain disabled.

## Cutover

1. Validate the preview's database integrity, collection, Browser Run, all four
   editions, RSS, Google-login form and callback destination, and recovery after
   a Worker version change. Keep preview Telegram credentials absent.
2. Commit and push to `main`, and wait for the exact commit's successful GitHub
   **Offline tests** push run before deploying production code. Bootstrap the engine with scheduling disabled
   and its `SITE` binding omitted, then the frontend with its public route omitted,
   then restore the engine binding. Keep those temporary configs beside the checked-in configs with an ignored
   `.env.bootstrap-*.json` filename (Python dependencies resolve relative to the config). Both service bindings must exist before importing.
3. Pause local writers through `cloudflare/cutover.py pause`. This acquires the
   installed deployment controller's lock and uses its existing stop method.
   Exit 75 means another deployment is active; wait and retry. The website stays
   available during the transfer.
4. Export a fresh snapshot with writers paused, import it into production, and
   compare database counts and published file hashes. Preserve the local data
   and previous deployment for recovery.
5. Route `rep0rter.observe.tw/*` to the production Worker, verify public pages and
   Google callback behavior, then deploy with `RUN_ENABLED=true`. Keep the local
   deployment timer disabled. Verify a complete Cloudflare reporting cycle and
   its health record before retiring the local web service. The protected
   `manage.py start` action can request the first alarm immediately; it retains
   the hourly duplicate-run guard.
6. Delete `MIGRATION_TOKEN` from production and preview. Record the Worker version,
   route, source commit, and health result. The unprotected `/healthz` exposes only
   runtime readiness and scheduling timestamps, never credentials or content.

The first scheduled request durably reserves its hourly slot. A crash retry does
not immediately rerun the entire pipeline; the next hour resumes the normal
cycle. Telegram's existing unknown-delivery handling remains in effect.

Native Workers replace host maintenance snapshots with Durable Object SQLite
point-in-time recovery. The old daily filesystem backups and SSH/rsync jobs do
not run in Workers. The current installation has no offsite destination, Notion
proposal discovery, or administrator alert transport configured. Configure and
verify any future equivalents explicitly before enabling those features.

Use Cloudflare version rollback for code recovery. Data persists independently
of Worker versions. Never restart the old local publisher after Cloudflare starts
writing without reconciling its database and delivery outbox. Before any Durable
Object point-in-time data restore, preserve the current exclusions ledger and
reapply it; restoring old data must not resurrect withdrawn material.

## References

- [Python Workers](https://developers.cloudflare.com/workers/languages/python/)
- [Python Flask support](https://developers.cloudflare.com/workers/languages/python/packages/flask/)
- [Durable Object SQLite storage](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/)
- [Browser Run Quick Actions](https://developers.cloudflare.com/browser-run/quick-actions/)
