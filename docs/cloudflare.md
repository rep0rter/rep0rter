# Cloudflare hosting and GitHub reporting

Production uses native Cloudflare Workers, without Containers or a Steam host.
GitHub Actions runs the hourly Python collector, model writer, Chromium card
renderer, and publisher. Cloudflare serves the site, runs Google login and
account features, and holds the durable SQLite database and generated assets.

## Reporting and storage

`.github/workflows/report.yml` runs hourly at minute 7 and supports manual
`report`, `build`, and `deliver` dispatches. GitHub cron can be delayed. The workflow requires
passing Offline tests for its exact main commit; the Worker also rejects a
runner whose source commit differs from the deployed engine.
An early engine health check reports both commit IDs before installing Python
dependencies and Chromium when deployment has fallen behind. Deploy tested main;
do not bypass the runner's version guard. Successful reporting runs print an
`Acceptance evidence` JSON record with aggregate editorial/collection observations
and the actual runner's request/article limits. It excludes source text,
identities, credentials, and raw upstream error messages. This is not a model
spending report and does not turn unmeasured missing events into zero.

To publish one existing website post to the configured Telegram channel, dispatch
`deliver` with its numeric `post_id`. It validates source policy and translation,
creates a durable outbox job, renders the card, and confirms the Telegram message
ID. Repeating a dispatch never resets sent or ambiguous jobs; reconcile uncertain
deliveries explicitly. Scheduled reporting continues to deliver new selected stories.

The runner takes an exclusive, expiring write lease and downloads a private
snapshot into its temporary workspace. Public pages remain available. Account
writes return HTTP 503 with Retry-After during the lease, rather than overwriting
concurrent changes. The runner renews the lease with progress; abandoned leases
expire after twenty minutes. GitHub concurrency prevents overlapping jobs.

Every SQLite commit synchronously checkpoints its changed 4 KiB pages to the
Worker before returning. Checkpoints have monotonically increasing sequence
numbers and idempotent retry handling. If persistence is uncertain, the runner
aborts. In particular, Telegram outbox reservations are durable before sending;
a runner crash cannot turn an ambiguous send into an automatic duplicate.

The snapshot and credentials are never uploaded as workflow artifacts. GitHub
secrets `REP0RTER_CONFIG` (the existing application configuration as JSON) and
`REP0RTER_RUNNER_TOKEN` authorize the runner. The same runner token is the engine's
`RUNNER_TOKEN` secret. Tokens are sent only in headers. Rotate both copies together.
`REP0RTER_CONFIG` also remains a Worker secret for account operations.

All writing and translation use `gpt-6-luna`. Local development reads `AI_MODEL`
from `.env`, with the same default in `Config`. The reporting workflow and both
Worker configurations explicitly set `AI_MODEL`; these public settings override
any older model value inside `REP0RTER_CONFIG` without replacing credentials.
Change the workflow and Worker configurations together when changing models.
The runner prints its effective model, and `/healthz` includes the Worker's
effective `ai_model`. GPT-6 Luna supports the existing Chat Completions request
and JSON text output; no prompt or API endpoint change is needed.

The engine's `Reporter` Durable Object stores database pages and files. The
separate JavaScript frontend's `PublishedSite` Durable Object atomically switches
public generations. Withdrawals persist their exclusions ledger and scrub the
public generation before attempting a new render. Native account submissions
can still use Browser Run; the large scheduled workload uses GitHub's Chromium.
Native Worker cron publication stays disabled to prevent duplicate schedulers.

The reporting workflow adds the public Code for Japan Slack archive through
`REPORT_ADDITIONAL_FEEDS`. The runner merges these URLs into `REP0RTER_FEEDS`
after loading the private configuration, preserving its existing sources and
credentials. This activates CfJ collection without replacing shared secrets.

## Code deployment

Install Node.js, `uv`, and `uv tool install workers-py==1.17.3`. Authenticate
Wrangler, merge changes through a pull request, and wait for the merged commit's
successful **Offline tests** push run. From its clean checkout:

```sh
python3 cloudflare/deploy.py --production
```

This checks remote main and the exact commit's CI, prepares a source-only bundle,
and deploys the backwards-compatible frontend before the engine. Verify `/healthz` reports that commit, then
run and verify the Production reporting workflow. Green CI alone is not a
production deployment. Future automatic code deployments need a separately
provisioned Cloudflare deployment API token; the reporting token cannot upload
Worker code. The old Steam deployment timer must remain disabled.

For local development, run `python3 cloudflare/prepare.py`, then `pywrangler dev`
from `cloudflare/`. The preview config has scheduling disabled. Bootstrap new
resources by creating the engine without its SITE service binding, creating the
frontend without its public route, then restoring the engine binding. Temporary
configs must sit beside the checked-in configs, under ignored `.env.*.json`
names, so Python dependency resolution uses the same project directory.

## Migration and verification

The `cloudflare/export.py` tool uses SQLite online backup and captures one site
generation, image cache, and exclusions ledger. Archives contain private account
and session records and must stay private. A temporary `MIGRATION_TOKEN` protects
`cloudflare/manage.py` import and validation operations. Import is allowed once
and must precede scheduler activation.

1. Validate the preview and merge tested migration code to main.
2. Run `cloudflare/cutover.py pause` under the installed Singa controller lock.
   Exit 75 means busy: wait and retry. Local writers stop; the website stays up.
3. Export a fresh snapshot, import it, and compare the database checksum/counts
   and public asset hashes. Never reuse a preview snapshot while writers run.
4. Deploy matching tested source, install runner secrets, and dispatch `build`.
   Verify all four editions, RSS, images, and Google callback.
5. Attach `rep0rter.observe.tw/*` to the frontend. Dispatch `report` and verify
   the workflow result plus the Worker's last_started/last_finished health data.
6. Retire local web through `cloudflare/cutover.py retire-web`; retain local data
   and recovery releases. Remove temporary migration credentials from both
   production and preview. Record source commit and Cloudflare versions.

Never restart the old publisher after Cloudflare starts writing without
reconciling the database and delivery outbox. Use Cloudflare code rollback for
code recovery; durable data is independent of Worker versions.

## Capacity and recovery

The in-memory database copy is capped at 32 MiB and individual files at 1.9 MB.
Runner transfer protocol 2 downloads the database in 1 MiB ranges and assets one
file at a time. Publication sends a validated manifest, uploads only missing or
changed files, and commits the complete generation. Each new transfer request
and response is bounded at 2 MiB. The engine's durable generation budget is
160 MiB including image cache and exclusions; the public generation budget is
128 MiB. A manifest contains at most 10,000 files and 2 MiB of JSON. These are
SQLite storage budgets, not whole-request or Python memory allocations.

The frontend reuses unchanged stored files and stages changed files in RPC batches
of at most 2 MiB. Only a complete generation switches the public site. Interrupted
transfers leave the previous public generation available. Retrying the same
manifest preserves successfully staged files; replacing it fences the older
token. Engine uploads expire after twenty minutes without progress. Expired engine staging is
removed lazily on the next authenticated transfer/start; finish removes staging
immediately. At most one bounded pending generation is retained. A committed
engine generation can retry its frontend transfer after a lost response. The
frontend pending generation has no TTL; the next publication replaces it. A
commit retry starts a fresh frontend transfer and reuses active-generation assets,
while the engine's already received upload files remain durable.

The old ZIP endpoints remain compatible, with their existing 32 MiB compressed /
64 MiB expanded limits. New runners explicitly request protocol 2 and require the
matching deployed source commit. Deploy the frontend before the engine through
the existing deployment script. Do not bypass the commit guard to mix versions.

Every new runner publication prints a `Capacity evidence:` JSON record before
upload. `protocol=2` records exact `database_bytes`, `expanded_bytes`,
`public_asset_bytes`, `public_asset_count`, and `largest_file_bytes`, alongside
their limits. ZIP bytes are absent because no complete ZIP is built. At 80% of a
limit, the runner emits a GitHub Actions warning. Logs contain only aggregate
numbers, never filenames, content, or credentials. Old run records use the old
ZIP limits; do not interpret those numbers against the new protocol's limits.

This transfer change addresses Actions publication and snapshot transport. Native
account `/submit` and `/write` still rebuild inside the Python Worker; their
existing asset hydration/rendering can retain a complete media cache in memory.
Do not infer that those routes are memory-safe at the larger durable budget.
See [the recovery and validation runbook](reporting-capacity-recovery.md) before
production rollout.

Native Browser Run calls honor transient rate limits and explicitly
fail if the daily browser quota is exhausted.

Durable Object SQLite point-in-time recovery replaces the old host's daily
filesystem/SSH backups. Preserve the current exclusions ledger before a data
restore and reapply it; old backups must never resurrect withdrawn material.
No offsite destination, Notion proposal discovery, or admin alert transport is
currently configured. Verify corresponding workflows before enabling them.

- [Python Workers](https://developers.cloudflare.com/workers/languages/python/)
- [Durable SQLite](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/)
- [Browser Run limits](https://developers.cloudflare.com/browser-run/limits/)
