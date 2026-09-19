# Japan source verification

Verified on 2026-09-19 with ordinary unauthenticated Python `requests` GETs. The strongest new source is Code for Japan's own public news JSON embedded in its HTML. No Notion credentials are necessary.

| Organization | Canonical page | Parse endpoint | Observed response | Recommendation |
| --- | --- | --- | --- | --- |
| Code for Japan | https://www.code4japan.org/news | Same page, `script#__NEXT_DATA__` JSON | HTTP 200, HTML; 404 news records | Active; implement dedicated JSON-in-HTML adapter |
| Smart Tokushima, formerly Code for Tokushima | https://note.com/codefortokushima | https://note.com/codefortokushima/rss | HTTP 200, `application/xml`; RSS 2.0, 18 items | Active feed, infrequent updates; existing RSS adapter |
| Code for Kanazawa | https://codeforkanazawa.org/ | https://codeforkanazawa.org/feed/ | HTTP 200, `application/rss+xml`; RSS 2.0, 10 items | Archive; existing RSS adapter, no recent news observed |

## Code for Japan public news data

The official [news listing](https://www.code4japan.org/news) exposes `JSON.parse(script#__NEXT_DATA__.textContent).props.pageProps.data`. Each record contains `id`, `slug`, `title`, `date`, `tags`, and an optional `thumbnail`. All 404 records had dates, with 404 distinct IDs. The page's `props.pageProps.max` is also 404: the HTML payload contains the whole observed index, even though the UI displays only a portion at once. No extra pagination request is needed for the observed payload; this is not a promise of permanent archive completeness.

Newest observed record: `2026-09-18T09:30:00.000+09:00`, ID `3ce3e971-0744-80a4-800a-ee9ab1aab661`, slug `narrative-observation-has-started`. Oldest observed: `2014-05-21T00:00:00.000+09:00`. The displayed article dates agree with the payload. These are publication dates, not fetch times. Of 404 records, 134 supply date-only `YYYY-MM-DD`; interpret their date in Japan (`Asia/Tokyo`, UTC+09:00) and explicitly mark day precision instead of claiming an exact original time.

Build article URLs as `https://www.code4japan.org/news/` plus the encoded slug for local slugs. Some `slug` values are already absolute external URLs (including official press releases and media appearances); preserve valid HTTP(S) URLs as the site's own destination and reject unsupported schemes. Do not concatenate an absolute URL onto `/news/`. IDs provide stable identities independent of title changes. The index supplies headline, tags, link and publication date, not article body; mark it as an index excerpt and do not invent content.

The corresponding public Next.js JSON endpoint also returned HTTP 200, `application/json`, with 404 `pageProps.data` records:

`https://www.code4japan.org/_next/data/ryUEC6hNoN5GJlr0Fk4VY/ja/news.json`

That build ID is deployment-specific, so do not hard-code it. Reading the embedded JSON from the canonical `/news` page is a single stable request. If a JSON-only request is desired, obtain the current `buildId` and `locale` from the page each time. The same URL without `/ja/` returned 404. No RSS alternate link was advertised, and `/rss.xml` and `/feed` returned 404.

A complete public fixture for implementation is saved outside the repository at `/tmp/code4japan-news.json`; `/tmp/cfj-news.html` contains its original HTML. Keep tests small and synthetic rather than committing the complete publisher archive.

## Smart Tokushima (formerly Code for Tokushima)

The [account RSS](https://note.com/codefortokushima/rss) identifies its channel as `一般社団法人スマートトクシマ（旧Code for Tokushima）`. Preserve the current organization name while retaining the historical affiliation. It covers civic-tech projects, disaster support, local data and the Urban Data Challenge.

The 18 observed items have `title`, HTML `description`, `pubDate`, `link`, `guid`, Media RSS thumbnail, and note-specific creator fields. Latest observed publication was `Wed, 17 Dec 2025 14:34:13 +0900`. The description is a short excerpt (546 characters for the first item), not a complete article. Standard RSS collection suffices. A feed may remain active even when its latest article is historical; retain original dates so it does not become new daily-report content during bootstrap.

## Code for Kanazawa

The [official WordPress feed](https://codeforkanazawa.org/feed/) supplies 10 historical civic-hacking reports. Latest observed: `Tue, 04 Feb 2020 00:50:48 +0000`, “Civic Hack Night 2020年1月レポート”. This is a useful archive but should not be described as a current news source. Standard RSS collection suffices and should not redate these items.

## Other probes excluded

`https://note.com/codeforjapan/rss` and `https://note.com/codefornerima/rss` returned 404; guessed account names should not enter the active catalog. `https://code4fukui.github.io/` returned HTTP 200 but did not advertise a feed, and this bounded investigation did not verify a dated structured source there.
