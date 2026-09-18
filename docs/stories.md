# Story identity and revisions

Issues #3 and #4 retain every observed source event while publishing one story
revision at a time. Existing post IDs, RSS GUIDs and Telegram message mappings
are preserved during idempotent bootstrap.

Identity uses an explicit public quoted-message reference or upstream canonical
object ID first. Otherwise it compares nearby source records (within seven days)
using normalized text and content URLs. URL normalization removes fragments and
known tracking parameters, sorts query parameters, and preserves parameters that
identify actual content. Bare URLs, Slack links and Markdown links participate.
Homepages, Telegram account pages and GitHub repository landing pages do not
identify an announcement. URLs do not dominate text similarity.

Different event dates and release versions remain separate stories even when
they share a registration or latest-release page. An explicit dated correction
can join its previous announcement. Similarity is conservative; arbitrary
multilingual paraphrases without a common source/reference are not presumed to
be the same event.

Copies and crossposts in the same or subsequent runs retain their source links
without generating another report. Historical fingerprints stop an old copy
from rolling a corrected story back. A material edit to the canonical source,
new collaboration document, participation restriction, cancellation, or dated
deadline update creates a new permanent revision. Simple thanks and unchanged
registration links do not. Thread context uses the latest 1,000 stored replies;
source scanning for inferred duplicate identity is bounded at 1,000 nearby
assigned records. Explicit source references are not subject to fuzzy matching.

A revision retains its real canonical root and material source IDs as writer
evidence. The synthetic revision event is a local publication identifier, not a
fabricated citation. Writer fallback quotes the actual material update with
attribution, prioritizing cancellation and participation restrictions, and holds
long or incomplete fragments for review rather than repeating the original
invitation. The site displays source links and revision history; source URLs
accept only HTTP(S) without credentials or control characters.

Privacy and automation exclusions run before identity assignment, thread
expansion, publishing, and source-link rendering. A share cannot revive a
withdrawn source. Both the source revision event and its post, story reservation,
and delivery job are committed in one transaction. SQLite uniqueness on
`(story_id, revision)` and `(story_id, fingerprint)` plus the serialized prepare
transaction prevent competing workers from reserving duplicate sends. Failed
reservation rolls the synthetic event, post and outbox job back together.

Offline tests cover same/across-run deduplication, dates/versions/venue false
matches, canonical root edits, repeated updates, collaboration/deadline/closed
participation changes, thanks, exclusion propagation, migration, unsafe URLs,
transaction rollback and competing workers. They make no network calls.
