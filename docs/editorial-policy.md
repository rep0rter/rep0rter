# Editorial selection and grounded writing

Issues #2 and #8 use a versioned policy (`eligibility-v2`) and writer contract
(`grounded-four-locale-v3`). New databases get append-only decision and writer
audit tables automatically when evaluated. Audit records contain source text;
they are internal operational records and must follow the same backup/access
and opt-out retention rules as the event store.

## Rollout and evaluation

`python -m rep0rter acceptance-report` combines editorial evidence with the
retained collection metrics and configured request limits. The production Actions
runner prints this aggregate after reporting, so Cloudflare observations can be
reviewed without exporting the private database. A reviewed decision remains
bound to its original evidence snapshot; a later unreviewed observation neither
erases it nor inherits its label. Latest-observation coverage is reported
separately from the latest reviewed snapshot per event.

`REP0RTER_EDITORIAL_MODE=shadow` is the default. Each eligible collection window
gets paired legacy and proposed scores, exact event/counter/container snapshots,
scoring time, all components, selection/exclusion reasons, and actual selection.
Shadow mode preserves baseline ranking while hard content/privacy/internal
channel gates apply in either mode. `active` enables the proposed selection:

- A verified root, at least 12 readable non-URL characters, explicit
  activity/resource/release intent, and a date or content link are required.
- Topic +4, date/link +2, at least 80 readable characters +1.
- Interaction contributes at most 2 points; membership/files/freshness add zero.
- Negated or merely quoted announcements, cancellations/delays, and stale
  "tonight" invitations are held for editorial review by the proposed policy.
- The 48-hour default window and five-item default batch cap remain configurable.
  Chinese compound words and English word boundaries avoid 松鼠/特徵/immigrant
  and URL-path keyword matches. Japanese/Korean explicit intents are supported.

Commands (wired by the CLI):

```sh
python -m rep0rter editorial-report
python -m rep0rter editorial-review 42 reject --note 'Missing participation context'
python -m rep0rter editorial-replay 42
```

The report explicitly identifies incomplete observation and unreviewed samples.
Leave paired observation running for **at least 14 days**, label both selected
and excluded examples, inspect channel-size coverage, false-positive and
false-negative labels, then decide whether to enable `active`. No code change or
synthetic fixture can substitute for elapsed observation or human judgments.
The fixture contains 27 minimal authored anonymous paraphrases and all seven
post verdict references; it is not a production DB export or historical replay.
Exact future replay uses only saved snapshots and the matching score version.

## Writer contract

Evidence records retain event IDs, source timestamps, URLs, authors, and bounded
text. Root evidence is followed by recent replies, with corrections prioritized;
older roots are not replaced by unattributed reply fragments. Story revisions
can supply canonical source events separately. Metadata provides publication
time, `Asia/Taipei`, and known author aliases. Source instructions remain data.

All four locales require nonempty strings, headline <=30 and summary <=90 Python
Unicode code points, no emoji/hashtags/relative dates/embedded URLs, no terminal
headline punctuation, and no redundant author attribution. Single-character
names use boundaries instead of global deletion. Structured `evidence_ids`,
`event_date`, `participation_url`, `needs_review`, and `review_reason` are checked;
unknown evidence IDs, unsupported dates/links, closed-event invitations, lost
speculation, and unattributed reply claims fail validation.

The writer retries at most once. It records mode, model, prompt version, every
validation error, evidence IDs, exact bounded input and output, and review state.
The fallback uses complete validated source clauses, prioritizes absolute dates,
and never cuts a date or URL to fit. When no safe excerpt exists, it creates an
internal review entry and no published draft. Missing translations remain
visibly missing; source excerpts are not mislabeled as translated text.

Translation backfill makes at most two model calls per post. The corrective call
contains only missing or invalid locales, the rejected text, validation reasons,
actual Unicode lengths, and the 30/90 limits. Valid saved editions and successful
first-attempt editions are preserved, including when the second request fails.
The translator preserves attribution, uncertainty, corrections and lifecycle
claims; it never truncates rejected text into a published translation. Backfill
changes saved translations and rebuilt site assets without sending messages.

Mechanical validation cannot establish semantic truth of every paraphrase or
infer unspecified locations. The model is explicitly forbidden from adding
facts, evidence snapshots allow review, and ambiguous cancellation/relative-time
cases are held. Long URLs stay in structured audit fields and source links.
The initial relative-date resolver handles explicit day offsets and month/day
references (including December-to-January); ambiguous weekends stay reviewable.

New public source adapters have no historical Slack scoring baseline: GitHub
items already admitted by the human-content source policy use bounded proposed
scores even during Slack shadow mode. Their public issue/release/PR URL is
source evidence, and typed admission does not require Chinese keywords.

The final per-run cap is applied after story deduplication. Decision rows record
actual selection after this step so repeated crossposts cannot starve distinct
stories. Privacy is rechecked against the root and all evidence immediately
before model invocation.

A model `needs_review` request is retained in writer audit provenance. Ordinary
fallback does not override it. For a material story correction, a separately
validated attributed extract can publish the source correction; the audit
explicitly records both the model's review request and this fallback decision.
