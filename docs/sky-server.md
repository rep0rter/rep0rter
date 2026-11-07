# Native sky-server deployment

Production runs rep0rter as native user systemd services on sky-server.
It uses the existing AI gateway ingress at `http://127.0.0.1:8317/v1`, with a
dedicated `rep0rter` client and model alias `sky-fast`. The gateway engines stay
on sky-mini and skyhong-blog. Cloudflare continues to provide DNS, TLS and Tunnel;
application compute, SQLite, generated files, reporting and backups move to the
host. GitHub still stores code and runs Offline tests.

The public origin switched on 2026-11-07 to the existing sky-server Tunnel.
The old public Worker route was removed; its runner and native scheduling are
disabled, and its account configuration is retired. GitHub variable
`REP0RTER_REPORTING_HOST=sky-server` keeps the legacy reporting workflow skipped.
The old durable database remains a private recovery copy, with no active writer.
Singa remains stopped.

The complete source snapshot preserved 263 posts, four accounts, one session,
the exclusion/tombstone ledgers, and both delivery outboxes. All file hashes,
SQLite integrity and table inventories passed before import. Verification covered
four language homepages and RSS feeds, permanent article pages, images and real
Google OAuth initiation. Completing Google sign-in still requires the person's
Google session. A real four-language translation and full native build passed;
the generated site was about 139 MB, above the former public generation limit.
Daily and weekly backups and a no-network restore rehearsal passed, preserving
every table and producing zero replay candidates. No offsite backup destination
is configured.

The first native reporting cycle completed successfully: 236 collected source
records, no new editorial posts, three repaired saved translations, and three
previously prepared Telegram deliveries confirmed. The two ambiguous Telegram
jobs and two ambiguous Threads jobs remained untouched. Collection remains
degraded: 18 Slack channels exhausted their bounded historical scan and one
returned duplicate pagination. Cursors remain preserved for later catch-up;
this does not mean the website or reporting process failed. Follow
`collector_health` in the private database for source progress. Translation
backfill of older missing editions is a separate operation.

The migrated Threads credential currently returns `token_invalid` (Graph code
190), including on a refresh attempt. Threads deliveries remain blocked until
the account is reauthorized; retain the existing prepared/ambiguous jobs rather
than resetting their state. Telegram delivery is working.

## Host layout and deployment

Private root: `/srv/rep0rter`, owned by `sky`, mode 0700. `service.json` contains
application settings. `data/` holds the authoritative SQLite database, exclusions
ledger, image cache and atomic site generations. `backups/` holds daily seven /
weekly four verified SQLite backups. `migration/` holds private migration material.
Never print these files or add them to Git.

`releases/<SHA>` contains a clean tested checkout and Python 3.12 virtualenv;
`current` selects the active release. The native deployment controller requires
the exact current main commit's passing **Offline tests** push run:

```sh
python3 scripts/host-deploy.py prepare --commit FULL_MAIN_SHA
python3 scripts/host-deploy.py activate --commit FULL_MAIN_SHA
```

Run the controller on sky-server. It shares `/srv/rep0rter/control.lock` with
reporting, manual builds and translation repair. Exit 75 means an operation is
active; wait and retry. Activation journals the old release, stops the website,
switches the symlink, starts the new website, and verifies its commit health.
Ordinary activation failure restores the previous application without restoring
the database. After an interrupted activation inspect `deployment.json` and
`current`, then rerun activation for the tested commit. Do not edit the journal
to pretend a deployment succeeded.

The website binds only `127.0.0.1:18191`; the existing sky-server Cloudflare
Tunnel forwards `rep0rter.observe.tw` to this port. The address avoids 18090,
which belongs to another application. Account routes, static files and Google
callback remain on the same public origin. Runtime health at `/healthz` reports
the source commit, model, systemd scheduler and reporting timestamps, without
provider error text or private database contents. `ready` describes serving
readiness; inspect collection status and completed cycles separately.

`rep0rter-report.timer` runs hourly at minute seven. Its oneshot service has no
automatic restart, preventing an automatic replay after an ambiguous delivery.
The existing durable Telegram/Threads outboxes remain authoritative. Both timers
remain disabled during preparation and data validation. Use the runtime wrapper
for manual operations:

```sh
/srv/rep0rter/current/.venv/bin/python /srv/rep0rter/current/scripts/host-runtime.py translate --limit 10
systemctl --user start rep0rter-report.service
systemctl --user start rep0rter-maintenance.service
```

Site generations switch atomically through the existing publisher. Native disk
files are not subject to Durable Object per-generation storage budgets. Host
units have CPU and memory limits; preserve sufficient disk space and observe
Chromium memory use during a real build.

## Migration sequence

1. Generate an RSA receiver key on sky-server, mode 0600. Set only its public
   key in GitHub variable `REP0RTER_MIGRATION_PUBLIC_KEY`.
2. Run **Encrypted host migration snapshot**. It acquires the existing source
   lease, exports all SQLite tables and files, and releases the lease. It never
   collects, writes articles, checkpoints, publishes, or sends notifications.
   The artifact contains RSA-OAEP / AES-GCM authenticated ciphertext only,
   including app configuration, delivery state and source access credentials.
3. Download and decrypt on sky-server using `scripts/cloudflare-snapshot.py`.
   Verify every file digest and SQLite integrity/inventory. Keep the original
   encrypted archive and a private database backup. Configure the project gateway
   key, `sky-fast`, and the existing public origin. Preserve Google and delivery
   secrets. Keep reporting and delivery disabled while testing a staging copy.
4. Prepare the tested native release and verify all editions, permanent pages,
   feeds, assets, Google login initiation, a real four-language translation and
   native site rendering. Do not send to public messaging channels in staging.
5. Set GitHub variable `REP0RTER_REPORTING_HOST=sky-server`; the legacy reporting
   job then skips. Confirm no reporting run is active. Fetch a **fresh** complete
   snapshot with `fetch --keep-lease` using private `runner.json`. This freezes
   source account writes while the lease is renewed by transfer requests. The
   lease expires twenty minutes after its last request; renew it during a long
   cutover. Never use the earlier rehearsal snapshot as the final database.
6. Import the final snapshot, preserve its outbox and exclusions, back it up,
   activate the host, then switch the Cloudflare Tunnel/DNS and remove the old
   public Worker route. Verify the public origin reaches the exact host commit.
   Disable the source runner and old account-write endpoints before releasing
   its lease. Keep Worker cron disabled and retain old durable data for recovery.
7. Enable the native reporting and maintenance timers; create `reporting-enabled`
   only after reporting scheduling is active. Verify a real reporting cycle,
   translation repair, completed publication, account flow and backups. Remove
   the temporary public-key variable and encrypted Actions artifact afterward.

Before the host accepts writes, a rollback can restore the old route and source
runner and release the lease. Once the host accepts writes or sends deliveries,
do not restore the stale Cloudflare database: first reconcile newer posts,
accounts, exclusions and outbox state. Old Singa services and its deployment
timer stay stopped throughout this migration.
