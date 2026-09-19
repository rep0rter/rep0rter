# Taiwan public feed research

Checked 2026-09-19 with anonymous HTTPS GET requests from this deployment. Counts describe the current feed window, not the full archive. No credentials or official API subscription are required.

| Publisher | Exact endpoint | Live result | Latest publication | Recommendation |
| --- | --- | --- | --- | --- |
| Open Culture Foundation (OCF) | https://ocf.tw/feed.xml | HTTP 200, `application/xml`, RSS 2.0, 20 items | 2026-09-15 00:00 +08:00 | **Active, strongest candidate.** Official site advertises this feed; open-source governance, civic technology and community work. |
| OCF blog | https://blog.ocf.tw/feeds/posts/default?alt=rss | HTTP 200, `application/rss+xml`, RSS 2.0, 11 items | 2026-09-10 04:58:07 UTC | **Active, lower editorial priority.** Official blog advertises this feed; recent entries mostly donation disclosures and financial reports. Keep distinct from the main site's news. |
| g0v.tw on Medium | https://medium.com/feed/g0v-tw | HTTP 200, `text/xml`, RSS 2.0, 10 items | 2020-12-17 05:38:13 UTC | **Archive.** Community project and summit context; useful historical material but not evidence of current activity. |
| Cofacts on Medium | https://medium.com/feed/cofacts | HTTP 200, `text/xml`, RSS 2.0, 10 items | 2020-09-28 16:14:28 UTC | **Archive.** Collaborative fact-checking project background and training; includes fact-check articles as well as project updates. |

All four endpoints fit the existing RSS collector. Medium needs `content:encoded` handling already present in the repository; its publication pages can return HTTP 403 while the anonymous RSS endpoint works. Preserve original publication dates and use normal freshness filtering so archive items are not represented as fresh news. Feed windows are finite and are not complete backfills.

OCF's Blogger Atom alternative is advertised at https://blog.ocf.tw/feeds/posts/default; the RSS endpoint above was the one live-validated for ingestion. Do not ingest both representations as separate sources.

## Other checks and limitations

- https://blog.g0v.tw/ redirects to `http://logdown.com/unavailable`; do not enable that old blog as a healthy current source.
- The [current g0v homepage](https://g0v.tw/) describes its monthly community updates as HackMD collaborative notes and links the [jothon organizer site](https://jothon.g0v.tw/). Neither homepage advertised a syndication feed in its HTML during this check. Do not guess a feed URL or assign publication timestamps to undated pages.
- https://cofacts.tw/ and `/about` returned HTTP 403 from this environment. The working Medium feed does not require bypassing that restriction. Cofacts also has [official open datasets](https://github.com/cofacts/opendata), but those contain fact-checking records rather than community project news and are not substitutes for the feed.
- [OCF's official Cofacts project page](https://ocf.tw/p/cofacts/) corroborates Cofacts' civic-tech relevance. [The g0v Medium publication](https://medium.com/g0v-tw) identifies itself as the Taiwan civic-tech community.

For immediate deployment, prioritize OCF's main feed; optionally collect its administrative blog with its own source identity. Keep the two Medium feeds explicitly labeled as archives.
