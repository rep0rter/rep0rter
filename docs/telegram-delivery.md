# Telegram delivery and recovery

New publication sends one source-text image and one localized caption per post.
Every caption links to that post's four language pages. `REP0RTER_TELEGRAM_LANGUAGE`
selects `zh-TW` (default), `ko`, `ja`, or `en`; missing translations are identified
in the caption. Text is shortened before HTML escaping to stay within Telegram's
1024-character photo caption limit. The complete text remains on the website.

Website posts and Telegram delivery are separate. `prepare_posts` commits each
new post and its Telegram outbox job in the same SQLite transaction. The site
can be built with no Telegram credentials; jobs stay `prepared`. After configuring
Telegram, the next real `run` or `report` also processes due jobs even if there
are no new stories. Dry runs do not create jobs or send. Test mode requires
`TELEGRAM_TEST_CHAT_ID`; it never falls back to `TELEGRAM_CHAT_ID`.

## Delivery states

- `prepared`: saved, awaiting a configured target and a publisher run.
- `sending`: claimed atomically with a five-minute lease. Other workers cannot
  claim this job. The exact caption, image path, content hash and target are
  saved before the request.
- `sent`: the remote message ID and chat ID were saved immediately, together
  with the post's delivery mapping, in one SQLite transaction.
- `failed`: a definite local preparation failure or explicit Telegram API
  rejection. HTTP/API 429 is retried on a later run after `retry_after`; other
  errors require fixing the problem and explicitly requeuing the job.
- `unknown`: a timeout, ambiguous API result, expired send lease, or failure to
  save a successful remote response. These jobs are **never retried automatically**.

This is not exactly-once delivery. Telegram and SQLite cannot commit together.
If Telegram accepts a photo and the process crashes before recording the response,
the operator must inspect the target chat. Re-running the publisher will not
silently resend that photo. A target configured when a job is created remains
attached to that job; changing the active chat does not redirect existing jobs.
Jobs created without a target bind to the configured chat on their first attempt.

## Inspect and reconcile

Stop concurrent workers while performing manual recovery. Back up the database.
Inspect job ID, post ID, target, status, error, message ID, and saved caption:

```sql
SELECT id, post_id, target, status, message_id, error, payload
FROM delivery_jobs ORDER BY id;
```

After checking the exact chat and caption, record an existing Telegram message
without sending anything:

```python
from rep0rter.delivery import DeliveryOutbox
from rep0rter.store import Store

with Store("data/rep0rter.sqlite") as store:
    DeliveryOutbox(store).reconcile(42, message_id=1234)
```

Only after confirming the message is absent (or fixing a definite failure),
explicitly make the job eligible for the next publisher run:

```python
with Store("data/rep0rter.sqlite") as store:
    DeliveryOutbox(store).reconcile(42, retry=True)
```

This API accepts only `unknown` or `failed` jobs. Wait for the lease to expire
and run `DeliveryOutbox(store).recover()` if a crashed job still says `sending`.
An incorrect decision to retry an ambiguous send can create a duplicate.

## Scope and existing data

New messages contain exactly one post, so modifying or deleting that Telegram
message cannot affect another post. Its `delivery.telegram` value stores
`chat_id`, `message_id`, and `outbox_id`; do not treat it as the legacy integer.
The complete payload is retained for audit/reconciliation. This release does
not add a Telegram edit/delete management command or migrate previously bundled
messages into per-post messages. Legacy posts are not silently sent again.
Withdrawal/reconstruction of old multi-post Telegram bundles remains work for
issue #13; inspect every post sharing a legacy message ID before editing it.

All automated delivery tests use temporary databases and mocked transports.
Do not use a production bot to test retries or unknown-state recovery.

The CLI exposes the same recovery operations, without sending during inspection:

```sh
python -m rep0rter outbox
python -m rep0rter outbox --job 42 --message-id 1234
python -m rep0rter outbox --job 42 --retry
```

`--retry` only prepares the job; the next real `report` or `run` attempts delivery.
