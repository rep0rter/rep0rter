# Threads delivery

Threads has its own durable queue. It does not change Telegram delivery. The feature is off by default and a code push alone will not publish anything.

Production uses the existing **Production reporting** GitHub Actions workflow for both Telegram and Threads. The same leased database snapshot and synchronous Cloudflare checkpoints preserve both channels' delivery records across runners. Do not create a second scheduler or run a local publisher against a copied production database.

Add the repository Actions secret `REP0RTER_THREADS_ACCESS_TOKEN`. Set repository Actions variables `REP0RTER_THREADS_USER_ID` (the ID verified with `/me`), `REP0RTER_THREADS_BATCH_SIZE=10`, and `REP0RTER_THREADS_ENABLED=1` after the tested main commit is deployed. The workflow supplies these as `THREADS_*` environment variables; the runner overlays only the Threads configuration, preserving the shared `REP0RTER_CONFIG` secret and Telegram settings. Setting the enabled variable to `0` pauses Threads without clearing its queue. The first manual `report` dispatch and subsequent hourly cycles publish at most one batch each.

Configure these values in the deployment environment, never in Git:

```text
REP0RTER_THREADS_ENABLED=1
REP0RTER_THREADS_USER_ID=<Threads account ID>
REP0RTER_THREADS_ACCESS_TOKEN=<Threads user access token>
REP0RTER_THREADS_BATCH_SIZE=10
```

The token needs `threads_basic` and `threads_content_publish`. Before sending, the worker reads `/me` and requires its ID to match `REP0RTER_THREADS_USER_ID`. It publishes text with `auto_publish_text=true`, then reads the remote post and saves its ID and permalink. The public article URL is the site's saved `posts/<id>/<zh-TW page>` URL. Posts are shortened to 500 UTF-16 units including that URL.

On each enabled `report`, `run`, or `loop` cycle, the worker scans saved eligible articles for missing Threads jobs. Existing jobs and posts with a recorded Threads delivery are skipped. Each cycle claims at most the configured batch size; more work waits for the next cycle. This includes older articles. **Enabling this feature will begin publishing the backlog**, so review the queue and batch setting first.

Commands:

```text
python -m rep0rter threads list --limit 30
python -m rep0rter threads send --post 12 --post 15
python -m rep0rter threads send --post 12 --post 15 --apply
python -m rep0rter threads outbox
python -m rep0rter threads deliver
python -m rep0rter threads reconcile --post 12 --remote-id <verified remote ID>
python -m rep0rter threads reconcile --post 12 --confirm-absent
```

`send` previews without an API call; `--apply` queues selected posts but does not send them. `deliver` scans and sends up to one batch. Failed API rejections are held unless the server explicitly rate limits, in which case the job waits for the retry time. A timeout, uncertain response, or expired send lease becomes `unknown` and is never automatically retried. Check the Threads account before using `--confirm-absent`; use `--remote-id` when the post exists. Remote verification is required before marking a job sent. Withdrawn or excluded posts are skipped before transport.

The Threads API and SQLite cannot provide a single atomic exactly-once transaction. If the remote accepted a post but the local save failed, leave the job for manual reconciliation. Never clear an `unknown` job without checking the account.

Accepted remote IDs are checkpointed before permalink verification. A failed read-back retains the ID for reconciliation and does not cause a new publish. Reconciliation verifies the account, author and exact queued text. Server errors and expired sending leases are treated as unknown, never automatically replayed. Withdrawals remove queued text; previously published Threads posts must be removed manually.

API reference: [Meta's Threads collection](https://www.postman.com/meta/threads/overview).
