# Verified civic-tech source catalog

Checked 2026-09-19. Ten agent tasks covered Korea, Japan, Taiwan, global organizations, Atom, JSON Feed, Code for Japan, discovery tooling, integration review, and the Civic Tech Field Guide adapter.

The checked-in [JSON catalog](../../rep0rter/feed_catalog.json) lists confirmed public endpoints. `active` means selected for ongoing monitoring, not necessarily recently published. Archives are supported but are not newly enabled by default. Existing Code for Korea Medium monitoring remains enabled. Directory discoveries are stored as ineligible for automatic news.

| Source | Region | Format | Use | Endpoint |
| --- | --- | --- | --- | --- |
| Code for Korea news | KR | rss2 | active | [code-for-korea-news](https://codefor.kr/boards/news.xml) |
| Code for Korea projects | KR | rss2 | active | [code-for-korea-projects](https://codefor.kr/boards/civic-tech-projects.xml) |
| Civic Tech Network | KR | rss2 | active | [civic-tech-network](https://civictech.kr/boards/news.xml) |
| Open Data Forum projects | KR | odf-rss | active | [odf-projects](https://www.odf.or.kr/archive-project) |
| Code for Korea Medium | KR | rss2 | archive | [code-for-korea-medium](https://medium.com/feed/codeforkorea) |
| Code for Japan community | JP | notion | active | [code-for-japan-notion](https://code4japan-community.notion.site/Home-9dd9cd85f07942c1bd5f6ef73efdb122) |
| Parti Coop | KR | rss2 | active | [parti-coop](https://coop.campaigns.do/boards/news.xml) |
| Parti blog | KR | rss2 | archive | [parti-blog](https://rss.blog.naver.com/partiunion.xml) |
| Information Disclosure Center | KR | rss2 | archive | [opengirok](https://www.opengirok.or.kr/rss) |
| Code for Japan news | JP | code4japan-json | active | [code-for-japan-news](https://www.code4japan.org/news) |
| Smart Tokushima (formerly Code for Tokushima) | JP | rss2 | active | [smart-tokushima](https://note.com/codefortokushima/rss) |
| Code for Kanazawa | JP | rss2 | archive | [code-for-kanazawa](https://codeforkanazawa.org/feed/) |
| Open Culture Foundation | TW | rss2 | active | [ocf](https://ocf.tw/feed.xml) |
| Open Culture Foundation blog | TW | rss2 | active | [ocf-blog](https://blog.ocf.tw/feeds/posts/default?alt=rss) |
| g0v Medium | TW | rss2 | archive | [g0v-medium](https://medium.com/feed/g0v-tw) |
| Cofacts Medium | TW | rss2 | archive | [cofacts-medium](https://medium.com/feed/cofacts) |
| mySociety | Global | rss2 | active | [mysociety](https://www.mysociety.org/feed/) |
| Open Knowledge Foundation | Global | rss2 | active | [okfn](https://blog.okfn.org/feed/) |
| Decidim | Global | atom | active | [decidim](https://decidim.org/blog/feed.xml) |
| Democracy Club | Global | atom | active | [democracy-club](https://democracyclub.org.uk/blog/feed/) |
| Code for All | Global | rss2 | archive | [code-for-all](https://codeforall.org/feed/) |
| Civic Tech Field Guide | Global | civictech-guide-json | directory | [civictech-field-guide](https://civictech.guide/) |

## Inspect and add sources

```sh
python -m rep0rter feeds catalog
python -m rep0rter feeds probe https://www.mysociety.org/feed/
python -m rep0rter feeds probe https://decidim.org/blog/
python -m rep0rter feeds probe https://www.code4japan.org/news
python -m rep0rter feeds probe https://civictech.guide/
```

The probe prints JSON and never opens the production database or publishes. For HTML pages it reports up to five publisher-advertised feed links, without guessing endpoints or subscribing. Probe a candidate to validate it, then add the chosen URL to `REP0RTER_FEEDS`. The catalog does not automatically subscribe to new sources. Public Notion URLs are recognized; their accessibility is checked by the existing collector rather than by this feed probe.

RSS 2.0, RSS 1.0, Atom 1.0 and JSON Feed 1/1.1 use one common storage, policy, deduplication and request-budget path. The `source=rss` identity is kept for compatibility. JSON Feed and RSS 1.0 are supported formats; this research did not find a new active source that requires them. Atom is used by Decidim and Democracy Club.

Code for Japan uses the public JSON embedded in its official news page. Open Data Forum uses its scoped RSS browser transport. Civic Tech Field Guide uses its documented public API, at most the latest 100 active directory listings per poll, with CC BY 4.0 attribution and links to the listings. This is a rolling discovery snapshot, not all 9,344 directory records or a complete archive. Added/created/modified dates never imply a project launch.

Original publication dates are preserved. Updated-only Atom and modified-only JSON Feed entries are stored but ineligible for automatic reporting. Future-dated entries wait until a subsequent poll after their date. Missing or malformed dates fail the snapshot rather than fabricating a date. Normal two-day collection does not backfill old articles.

## Research and exclusions

- [Korea](01-korea.md)
- [Japan](02-japan.md)
- [Taiwan](03-taiwan.md)
- [Global](05-global.md)

These reports document response counts, date fields, source attribution and unsuccessful probes. Unverified, inaccessible, repurposed and irrelevant domains are excluded. No account token is needed for the listed public sources. Source content remains evidence to evaluate, not instructions to execute.

## Budget and deployment

Bounded GitHub/feed/Notion jobs run before Slack with the existing fair share caps. Slack receives unused requests for its incremental backlog; all traffic still consumes the same per-run/daily budgets. Failed snapshots or pending Slack gaps remain visible in health. New sources require worker recreation after configuration changes.
