# Reporting publication capacity recovery

The October 18 audit confirmed the same failure in Production reporting runs
39061679297, 39051985923 and 39037392299: `Worker publish failed with HTTP 413`.
The latest aggregate measurements were:

| Measurement | Observed bytes | Previous bound |
| --- | ---: | ---: |
| Checkpointed SQLite | 19,685,376 | 33,554,432 |
| Complete compressed ZIP | 56,376,503 | 33,554,432 |
| Expanded generation | 88,038,483 | 67,108,864 |
| Public assets | 86,509,054 | 67,108,864 |
| Largest individual file | 605,114 | 1,900,000 |

Collection/model credentials are not established causes of these failures. The
database was checkpointed before publication failed. Successful report/delivery
completion was not reached. Do not dispatch paid reporting merely to reproduce
the size rejection, reset delivery records, remove permanent story pages, or
restart the retired host scheduler.

Protocol 2 removes the complete ZIP from both publication and the next runner's
snapshot download. The 128 MiB public / 160 MiB combined limits bound durable
SQLite storage; 1.9 MB files, 2 MiB requests/responses and 1 MiB database ranges
bound transfer buffers. There is no R2, KV or Pages API on this route. Engine and
frontend each hold an active and potentially one staged generation in SQLite.
Native SQL copies and commits avoid materializing the complete generation in
Python. These offline tests do not measure production Durable Object SQL CPU,
billing, network duration, or Pyodide memory.

| Route / path | Change | Remaining validation |
| --- | --- | --- |
| Actions `start` / `snapshot` | Manifest + bounded database/file downloads | Real snapshot duration within 30-minute Actions budget |
| Actions `publish-begin/file/commit` | Hash/size/path validation, durable staging and retry | Real DO SQL duration and memory |
| Engine → frontend | Existing 2 MiB batches and atomic switch; 128 MiB storage budget | Public health/version and all editions |
| Legacy ZIP `start` / `publish` | Existing protocol retained | Remains unsuitable for the oversized generation |
| Account `/submit`, `/write` | No behavior change in this package | Existing full media hydration/rendering may exceed Worker memory |
| Native Worker cron | Remains disabled | Never introduce another publisher |

Before production rollout, review the native-account memory limitation separately.
The transfer package alone is not proof of end-to-end recovery. Follow the normal
clean main / exact-commit Offline tests / frontend-first deployment process in
`cloudflare.md`. The code version guard blocks runners while main and engine
differ; do not bypass it.

After approved deployment, prefer a controlled `build` workflow to exercise the
existing checkpointed database without collecting, generating model text, or
sending Telegram/Threads messages. It still renders cards locally in Chromium.
Verify the complete workflow, protocol-2 aggregate capacity, all four public
editions, feeds, representative permanent story/card URLs and deployed SHA.
Observe the existing scheduled reporting cycle afterward; require `last_finished`
to advance before declaring reporting healthy. Keep readable-site readiness
separate from reporting freshness.

For code rollback, keep the current durable database and exclusions ledger. An
old engine/frontend can continue serving the already stored files; the old
runner's complete-ZIP pipeline cannot recover the oversized generation. Never
roll the database back to make capacity fit or resume the retired publisher.
After an interrupted frontend transfer, retry commit with the same active lease
and publication token; a new lease fences old writes and starts clean. Public
content remains on the last complete frontend generation until a full commit.
