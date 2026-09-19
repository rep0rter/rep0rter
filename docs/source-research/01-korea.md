# Korean civic-tech sources

Verified 2026-09-19 with ordinary anonymous HTTPS GETs, certificate verification enabled. These candidates complement the already configured Code for Korea, Civic Tech Network, Open Data Forum and Code for Korea Medium sources. Item counts describe the current rolling feed, not complete archives.

| Organization / source | Canonical page | Exact endpoint | Live result | Recommendation |
| --- | --- | --- | --- | --- |
| Parti Coop / 빠띠쿱 news | https://coop.campaigns.do/boards/news | https://coop.campaigns.do/boards/news.xml | HTTP 200, `application/xml`, RSS 2.0, 12 items; latest `Fri, 18 Sep 2026 13:05:04 +0900` | **Active**: strong civic-tech relevance; use existing RSS collector |
| Parti / 시민활동플랫폼 빠띠 blog | https://blog.naver.com/partiunion | https://rss.blog.naver.com/partiunion.xml | HTTP 200, `text/xml`, RSS 2.0, 40 items; latest `Tue, 10 Dec 2024 09:00:00 +0900` | **Archive**: public participation tools and townhall manuals; existing RSS collector works |
| Center for Freedom of Information / 투명사회를 위한 정보공개센터 legacy site | https://www.opengirok.or.kr/ | https://www.opengirok.or.kr/rss | HTTP 200, `text/xml`, RSS 2.0, 10 items; latest `Thu, 5 Oct 2023 11:38:37 +0900` | **Archive**: FOI, open data and civic oversight; existing RSS collector works |
| KOSIS statistics portal notices | https://kosis.kr/serviceInfo/rssGuide.do | https://kosis.kr/rss/notice_rss.jsp | HTTP 200, misleading `text/html` content type but RSS XML; 13 items; latest `2026-09-08 11:00:00.0` | **Hold**: needs explicitly configured Korean local timezone/date parsing; broader data-provider notices rather than civic-tech organization news |

## Evidence and parser fit

The [Parti official domain](https://parti.coop) redirects to `https://coop.campaigns.do/`. Its [news board](https://coop.campaigns.do/boards/news) explicitly advertises the exact RSS URL in an HTML alternate link. Its categories include public-interest data, Townhall and participation platforms. Items contain `title`, `description`, `pubDate`, `link`, `guid`, sometimes `image`. The first three descriptions are exactly 200 characters; preserve them as excerpts. The existing `rep0rter.collectors.rss.to_event` parsed **12/12** current items successfully. This is the immediate activation recommendation.

The Parti site's footer links the Naver blog, establishing organizational attribution. Naver RSS provides `author`, `category`, `title`, `link`, `guid`, `description`, `pubDate` and additional activity namespace fields. Existing parsing succeeded for **40/40** items. Retain original dates and avoid presenting these older manuals as current releases. The primary news feed above is the better monitoring source. Naver links contain RSS tracking parameters; these are not a reason to alter publication dates or identifiers.

The [Information Disclosure Center legacy homepage](https://www.opengirok.or.kr/) matches its feed, including October 2023 latest material and an under-construction banner. Posts cover OpenWatch public oversight data/API and related civic work. RSS supplies `title`, `link`, `description`, `category`, `author`, `guid`, `comments`, `pubDate`; existing parsing succeeded for **10/10** items. Descriptions include substantial HTML, which the existing sanitizer normalizes. This is useful historical context, not proof the organization ceased publishing elsewhere.

KOSIS [official RSS documentation](https://kosis.kr/serviceInfo/rssGuide.do) supplies the exact endpoint. Its `pubDate` values use SQL-like local timestamps with whitespace and no offset, unlike valid RFC 822 dates. Do not silently assume UTC or use the collection date. No endpoint-specific date workaround was added in this research task; hold this feed until that contract is explicit. General statistics portal events are also less focused than community project news.

## Excluded legacy candidates

- `https://codenamu.org/` returned HTTP 200 but its current page title/content are unrelated gambling promotion. Do **not** add this formerly relevant domain based on its name or old lists.
- `https://codeforseoul.org/` failed to connect in this environment; no usable feed verified.
- `https://parti.xyz/` and `https://okfn.kr/` failed TLS verification in this environment. No insecure TLS fallback was attempted. Use the verified Parti Coop canonical site above.

No official API token, browser cookie, login, posting action, or production database write was used. Live parsing checks called the existing pure RSS normalization function and did not collect events into production.
