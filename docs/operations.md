# Operations: backups, health, monitoring and latency

`health` and `metrics` use SQLite `mode=ro` + `query_only`; neither constructs a
Store, migrates a schema, collects, calls an LLM, nor publishes. The CLI skips
`ensure_dirs` for these commands. A successful process or an empty successful run
is not evidence of healthy collection: the collector must explicitly persist
`collector_health.last_healthy_at` after validating its sources.

## Deployment

Singa automatically deploys tested pushes to `main` using a host-side systemd
timer. See [automatic deployment](deployment.md) for setup, health checks,
rollback behavior and operator commands.

Compose declares worker and web healthchecks and the existing log policy
(`local`, `max-size=20m`, `max-file=3`). An unhealthy status is monitoring evidence;
Docker does not automatically restart a running unhealthy worker. The separate
maintenance service runs hourly, creates a verified snapshot daily, and rehearses
restoration every 28 days. The web container mounts the data directory read-only
so replacing `site` with a release symlink is visible without remounting.

The public JSON export workflow is explicitly named **not production backup**.
Push/PR CI runs offline Python tests without dotenv or network access. Remote CI
results must be checked after pushing; configuring CI is not proof it ran.

## Consistent backups

```sh
python -m rep0rter backup
python -m rep0rter backup --offsite backup@backup-host:/dedicated/rep0rter
python -m rep0rter restore-rehearsal data/backups/daily-YYYY-MM-DD.sqlite \
  --policy data/exclusions.json
```

Snapshots use SQLite's backup API, including committed WAL data, and are checked
with `integrity_check` before atomically replacing a daily file. The backup
contains every SQLite table (events, posts, delivery jobs, cursor state, decisions,
exclusions, runs, and future tables). A manifest includes UTC creation time,
`user_version`, schema hash, row counts, and SHA-256 of the snapshot. The current
policy ledger has its own `.policy.json` companion; it must never replace a newer
live policy ledger during recovery.

Local retention is the latest **7 daily + 4 ISO-weekly** snapshots. Permissions
for snapshots/manifests are private. Weekly snapshots are the first successful
snapshot that week. Configure `REP0RTER_BACKUP_OFFSITE=backup@host:/directory` and
an explicitly mounted read-only SSH configuration with verified `known_hosts`
for a separate host. `rsync` and `ssh` must be installed. No destination is
invented, no host key checking is disabled, no remote files are deleted. Apply
remote retention at that destination if needed. Off-host backups are optional by the operator's explicit choice. Health only
requires an offsite copy when `REP0RTER_BACKUP_OFFSITE` is configured or
`health --require-offsite` is requested; local daily/weekly backups and restore
drills are the default. A local snapshot is never described as an off-host copy. Backup/rsync failures do not advance the
maintenance success marker and are retried next hour.

The restore drill creates a new temporary database, checks checksum, integrity,
every table's content and counts, and verifies already-posted events cannot enter
the unposted selection. It then copies the **current** policy ledger into the
isolated directory and initializes the store to reapply current tombstones. A
missing/invalid current ledger fails closed. It does not start a collector,
renderer, Telegram sender or scheduler, and deletes the temporary directory.
Outstanding delivery jobs retain their status; the drill never sends them.

Operational targets are RPO <= 24 hours and RTO <= 2 hours. These are targets, not
claimed measurements. Recovery should stop worker and maintenance, preserve the
current exclusions ledger separately, restore into a new directory, run the
drill and health checks, rebuild the site, then start the worker. Never restore
an old exclusions companion over a newer live ledger. Keep external policy
copies current on the backup host to survive loss of the primary host.

## Withdrawal during rendering failures

Withdrawal immediately sanitizes every current and retained site generation under
the site build lock, before requesting a full rebuild. Matching articles and RSS
GUIDs are removed, permanent pages become generic four-language notices without
personal metadata, and derived image caches are purged. These are atomic per-file
replacements, so a later renderer failure does not keep withdrawn text online.
Surviving article text and link targets remain intact. A subsequent successful
build restores the normal layout and regenerates allowed cards.

## Read-only health and explicit administrator alerts

```sh
python -m rep0rter health --url https://rep0rter.observe.tw
python -m rep0rter maintenance                 # evaluates, backs up; does not send
python -m rep0rter maintenance --send-alerts   # explicit administrator transport
```

Checks include DB readability, verified healthy collection within 2.5 hours,
latest degraded collection, three consecutive failed runs, unfinished runs over
2.5 hours, negative source/first-seen delay, free disk reserve (default 512 MiB),
local backup age <= 26 hours (also offsite age when explicitly configured), HTTP response and RSS parsing. The feed's newest
publication is compared with the persisted latest post, so a legitimately quiet
community does not trigger a stale-news alarm. Collection freshness remains a
separate check. Invalid source pages must not advance healthy collection state.

Actual notifications require **both** `REP0RTER_ADMIN_BOT_TOKEN` and
`REP0RTER_ADMIN_CHAT_ID`, with a destination different from news/test channels,
and `--send-alerts` or `REP0RTER_SEND_ADMIN_ALERTS=1`. There is no fallback to the
public Telegram target. This implementation does not send any notification while
being installed or tested. The state machine emits one new-condition alert,
throttles repeats for 6 hours, and emits one recovery. Tests simulate three
failures and recovery without any network. Preview state cannot suppress actual
alerts. Transport errors report their class without credential-bearing URLs.
Monitor the monitoring service externally as well; no in-process monitor can
report a total host outage.

## Reproducible latency metrics

```sh
python -m rep0rter metrics > latency.json
```

Output states the observation interval, sample counts, bootstrap-known/unknown
counts and bootstrap fraction, and uses **nearest rank** (`ceil(p*n)`) for P50 and
P95. Values are seconds, with the proportion over two hours. Groups distinguish
source, root/reply kind, known bootstrap and known failure-recovery samples; true
failure recovery remains included rather than being dropped from the SLO.

- Acquisition = event `first_seen - source timestamp`. This is total observed
  lag, not an estimate of archive synchronization latency.
- Eligibility = earliest recorded eligible editorial decision minus first seen.
- Selection = earliest selected decision minus first seen (distinct from merely
  passing a threshold when the per-run limit excludes an item).
- Publication = persisted `posts.published_at - first_seen`.
- Delivery = confirmed outbox `updated_at - published_at`; historical deliveries
  without an observation are unknown, never imputed from publication time.

Historical bootstrap, eligibility and delivery observations are marked unknown.
Negative delays are reported separately as clock skew and are excluded from
quantiles rather than clamped to zero. Run duration is separate from acquisition.
The default collection interval remains one hour. Collect at least two weeks of
these metrics before choosing a shorter interval, and respect per-source request
budgets when changing it.

## 來源提案（每週）

設定 `REP0RTER_NOTION_PORTAL` 後，`maintenance` 服務每週從公開的 Notion 入口取出
GitHub 允許清單候選，並以既有的管理者告警路徑（`alert_transitions` 去重節流、
`send_admin_alerts` 只送管理目的地）通知。詳見 [FtO 來源](fto-sources.md)。

- **不進入 health。**worker 的 healthcheck 執行 `rep0rter health`，若把提案寫進
  `check_health` 的 `issues`，別人編輯自己的 wiki 就會讓容器變成 unhealthy。
  提案有自己的報表與 `proposals.json` 狀態檔。
- **不自動設定。**提案只列出不在 `REP0RTER_GITHUB_REPOS` 內、也不在排除台帳內的
  repository，由人審核後手動加入。加入後下一輪提案消失並送出 recovery 通知。
- **可以記錄「不納入」。**`exclusion add --scope container --subject github:owner/repo`
  同時讓該 repo 不被採集也不再被提案，理由寫在 `--reason`。不另開第二份清單。
- **失敗被隔離。**發現用的是未文書端點，壞掉是預期內的事。任何失敗都不會影響
  備份、還原演練或健康檢查，`maintenance.json` 的紀錄也不會因此遺失。
- **不佔用採集預算。**發現與 `collect_all` 的每輪／每日 request budget 完全分離。
- 未設定 `REP0RTER_NOTION_PORTAL` 時不做任何發現。
