# FtO: a three-hour civic-tech news workshop

Verified against public first-party pages and anonymous GitHub APIs on **2026-09-22**. This is a proposal for the next shared work session, not a claim that a session happens tomorrow: the [official Seoul event page](https://fto.asia/events/seoul-2026.html) lists **September 18–20, 2026**, at Haja Center. Confirm the actual meeting date with participants.

**Suggested goal: bring three useful Japanese/Korean project updates through rep0rter, with original evidence, four readable editions, and correctly attributed images.** English defaults, Chinese/Japanese/Korean switching, report cards, source avatars/logos, and bot filtering already exist. Spend the session on source coverage and editorial quality, then test the existing presentation with native speakers. See the [current source catalog](fto-sources.md) and [research notes](source-research/README.md).

## Five projects worth discussing

Dates below are publication dates or GitHub `merged_at` dates in UTC, not repository push dates. A merged PR establishes a code change; it does not establish production deployment or independently verified impact.

| Project | Concrete progress and evidence | Useful conversation |
| --- | --- | --- |
| **BirdXplorer — Japan** | Human-authored [PR #292](https://github.com/codeforjapan/BirdXplorer/pull/292), merged **September 16**, changes Community Notes ingestion to handle concurrent writers. The author reports missed new notes and explains the repair. No GitHub releases returned by the release API on September 22. | Report data completeness and source freshness in language readers understand; ask maintainers what counts as a meaningful public update. |
| **Mapprint — Japan** | Human-authored [PR #563](https://github.com/codeforjapan/mapprint/pull/563), merged **August 1**, adds a Kumamoto disaster paper-map configuration with filters for reuse permission and facility opening status. The more recent [August 11 PR #566](https://github.com/codeforjapan/mapprint/pull/566) only repairs a README logo path. No GitHub releases returned. | Use the August feature as context, not fresh September news. Discuss disaster-data attribution, permission changes, and corrections after printing. |
| **Where Is My Bus — Korea** | Human-authored [PR #19](https://github.com/Code-for-Korea/where-is-my-bus/pull/19), merged **September 18**, makes driver registration PINs single-use and fixes retry behavior after failed Traccar integration. Its manual test checklist remains unchecked; do not report those tests as passed. No GitHub releases returned. | Ask for a user-facing description of the transport problem and confirmation of any actual rollout. Combine related changes into one story instead of three PR notifications. |
| **Decidim CFJ — Japan** | Human-authored [PR #902](https://github.com/codeforjapan/decidim-cfj/pull/902), merged **September 19**, enables images in meeting agendas. A separate [v1.21.2 release](https://github.com/codeforjapan/decidim-cfj/releases/tag/v1.21.2) was published that day; its notes cover account-import and sidebar fixes. Do not assume the agenda change is in that release from timestamps alone. | Review accessible civic-participation announcements and image attribution; distinguish human feature work from routine refactors and automated release packaging. |
| **Parti — Korea** | A [September 18 organizational report](https://coop.campaigns.do/posts/1RtJ5D4) describes Parti's participation in a **September 9** parliamentary discussion about public officials' hate speech and civic participation. This is a program update, not a software release or a future invitation. | Add non-code civic-tech outcomes: public deliberation, facilitation, public-interest data, and lessons from participation platforms. |

## Working update sources and collection methods

The following endpoints returned HTTP 200 during this check. The four PR lists were inspected for human account authors and non-null `merged_at`; release APIs were checked separately. This was a bounded scan of 25 recently updated closed PRs per repository, not a complete archive audit.

| Source | Verified endpoint | How to use it |
| --- | --- | --- |
| BirdXplorer | [Closed PR API](https://api.github.com/repos/codeforjapan/BirdXplorer/pulls?state=closed&sort=updated&direction=desc&per_page=25), [releases](https://api.github.com/repos/codeforjapan/BirdXplorer/releases?per_page=1) | Keep concrete human changes; skip dependency bots and raw pushes. |
| Mapprint | [Closed PR API](https://api.github.com/repos/codeforjapan/mapprint/pulls?state=closed&sort=updated&direction=desc&per_page=25), [releases](https://api.github.com/repos/codeforjapan/mapprint/releases?per_page=1) | Preserve actual dates; a quiet project is not a reason to republish historical changes as news. |
| Where Is My Bus | [Closed PR API](https://api.github.com/repos/Code-for-Korea/where-is-my-bus/pulls?state=closed&sort=updated&direction=desc&per_page=25), [releases](https://api.github.com/repos/Code-for-Korea/where-is-my-bus/releases?per_page=1) | Group related PRs and preserve the distinction between author claims and checked deployment evidence. |
| Decidim CFJ | [Closed PR API](https://api.github.com/repos/codeforjapan/decidim-cfj/pulls?state=closed&sort=updated&direction=desc&per_page=25), [releases](https://api.github.com/repos/codeforjapan/decidim-cfj/releases?per_page=1) | A release generated by automation can document real changes; do not turn every automation event into its own story. |
| Parti news | [RSS](https://coop.campaigns.do/boards/news.xml) | Existing RSS adapter; preserve publication time and original article link. Descriptions are excerpts, not full articles. |
| Code for Korea | [News RSS](https://codefor.kr/boards/news.xml), [project archive RSS](https://codefor.kr/boards/civic-tech-projects.xml) | Discover projects and community outcomes beyond GitHub. Archive listing dates do not establish product launch dates. |
| Code for Japan | [Official news index](https://www.code4japan.org/news) | Existing dedicated adapter reads embedded Next.js JSON. Do not hard-code the changing Next.js build ID. |

For follow-up people and events, use the community links on the [FtO website](https://fto.asia/) and [Code for Japan's international community page](https://www.code4japan.org/activity/global). Ask participants which **public** update channel they maintain and how corrections or opt-outs should reach them. Membership in a chat does not authorize publishing its private conversation or member directory.

## Three-hour session

| Minutes | Shared work | Reviewable output |
| --- | --- | --- |
| 0–20 | Each country brings one recent useful update and one noisy example. Agree who reads the digest and what deserves publication. | Six examples, with reasons to include or reject. |
| 20–55 | Pair a project maintainer with a contributor. Confirm a public URL, update endpoint, source language, stable identity, original date, avatar/logo attribution, and correction contact. | Three reviewed source records; identify gaps in the existing catalog instead of adding duplicate feeds. |
| 55–110 | Integrate one uncovered public source, or improve one incomplete source. Use existing RSS/GitHub adapters first; run against an isolated database. | A small PR with a representative fixture, repeat-fetch deduplication check, and visible failure handling. No silent conversion of errors into “no updates.” |
| 110–155 | Japanese/Korean speakers review three drafts in all four languages; inspect mobile cards and original-source attribution. Include cancellation/closed-event wording and a missing-avatar case. | Corrected drafts and regression examples. A missing photo should become an honest fallback, not an invented person. |
| 155–180 | Demonstrate the complete pipeline and decide ownership of follow-up sources and editorial review. | Three approved preview stories, one merged-ready PR, and named follow-up tasks. Public posting follows normal deployment and publication controls. |

Success means useful coverage and faithful attribution, not maximizing message count. Track country/source diversity, rejected bot noise, duplicates, missing translations, and source failures for the following week. This research made no production, source-configuration, or messaging changes.
