# Global civic-tech public sources

Checked 2026-09-19 using anonymous HTTPS requests and the repository's `rep0rter.collectors.rss.parse_feed`. Counts are the current response window, not a full historical archive. Each successful feed below parsed every entry without additional requests or credentials. Atom support was present in the shared working tree during validation.

| Organization and canonical page | Exact endpoint | Live result | Latest original publication (UTC) | Recommendation and relevance |
| --- | --- | --- | --- | --- |
| [mySociety](https://www.mysociety.org/blog/) | https://www.mysociety.org/feed/ | HTTP 200; RSS 2.0; 10 entries | 2026-09-14 09:49:08 | **Active.** Civic participation, public-information access, FixMyStreet, and the international TICTeC community. Official blog advertises this feed. |
| [Open Knowledge Foundation](https://blog.okfn.org/) | https://blog.okfn.org/feed/ | HTTP 200; RSS 2.0; 10 entries | 2026-09-18 10:34:07 | **Active.** Open government data, open infrastructure, and international civic-tech community projects. Keep editorial selection focused on civic relevance within its broader open-knowledge remit. |
| [Decidim](https://decidim.org/blog/) | https://decidim.org/blog/feed.xml | HTTP 200; Atom 1.0; 6 entries | 2026-07-06 00:00:00 | **Active.** Open-source participatory democracy platform and its international deployments. The official HTML advertises the endpoint as RSS, but the actual XML is Atom. |
| [Democracy Club](https://democracyclub.org.uk/blog/) | https://democracyclub.org.uk/blog/feed/ | HTTP 200; Atom 1.0; 5 entries | 2026-07-31 09:39:42.203506 | **Active.** UK election information tools, accessibility and public electoral data. Official blog footer links the feed. |
| [Code for All](https://codeforall.org/blog) | https://codeforall.org/feed/ | HTTP 200; RSS 2.0; 10 entries | 2023-07-12 12:52:56 | **Archive.** Directly relevant international civic-tech network, but its current site/feed snapshot is historical. Do not present entries as current activity. |
| [Civic Tech Field Guide](https://civictech.guide/build) | https://civictech.guide/api/v1/projects/search?status=Active&sort=newest&limit=5 | HTTP 200; bespoke JSON; 5 records, `meta.total=9344` | Latest listing added 2026-09-18; `created_at=2026-09-18T15:10:51.749Z` | **Active directory; requires its own adapter.** Global civic-tech project discovery, not a standard JSON Feed or project-news stream. No key is required for public reads. |

## Ready for the unified feed list

Enable the first four endpoints as distinct public sources. The existing RSS converter handles the two RSS feeds, including WordPress `content:encoded`; the added Atom converter handles both Atom endpoints. Stable IDs, original publication timestamps, normalized text, canonical links, and the ordinary collection freshness window remain applicable. These small rolling windows are not evidence of a complete historical backfill.

Code for All can be retained in an explicitly historical source catalog. Its [official homepage](https://codeforall.org/) links an additional Medium publication whose feed, https://medium.com/feed/code-for-all, also returns HTTP 200 and 10 parseable RSS entries; latest publication is 2022-02-02 12:10:29 UTC. Prefer the organization's main-site feed if choosing only one historical source.

## Civic Tech Field Guide JSON adapter contract

The publisher's [build documentation](https://civictech.guide/build) documents public reads and links its [OpenAPI specification](https://civictech.guide/api/openapi.json). This API is not the JSON Feed standard, so do not add it as an ordinary RSS/Atom/JSON Feed URL without an adapter.

- Response envelope: `data` array and `meta.total`. Pagination is `limit` (maximum 100) and `offset`; `sort=newest` sorts by date added. Supported `status` filters are `Active`, `Inactive`, `N/A`, and `all`.
- Record identity: UUID `id`, `airtable_id`, and `slug`. Use the stable record ID for ingestion identity; multiple records may share a project website URL (observed in this sample).
- Useful public fields: `title`, `description`, `longDescription`, `url`, `repository_url`, `categories`, `tags`, `location`, `projectTypes`, and `organizationType`.
- Preserve date semantics: `added` is the day the listing was added; `created_at` is a database timestamp; `lastModified` describes listing edits; `founded` is a project year. None is proof of a project launch or news publication date. Store as directory discoveries with explicit timestamp provenance, not fresh project announcements.
- Credit the Field Guide and link back to its listing. The publisher's documentation labels its data CC BY 4.0.
- This endpoint includes civic-adjacent projects, events, and organizations. Use categories and project types for editorial relevance; a directory listing alone does not establish the correctness of its claims.

## Not enabled after verification

- **Code for America:** [official news page](https://codeforamerica.org/news/) is relevant and current in browser search, but both that page and `https://codeforamerica.org/feed/` return HTTP 403 to the normal HTTP client in this environment. Do not enable the unverified feed or work around access restrictions.
- **Democracy Works:** `https://www.democracy.works/blog?format=rss` redirects to its HTML `/news` page; it is not RSS. No alternate feed was advertised in the returned HTML.
- **Code for Africa:** `https://codeforafrica.org/feed/` returns HTTP 404. No claim of a working feed is made.
- **Civic Tech Field Guide RSS guess:** `/feed/` redirects to `/feed` and returns HTTP 404; use the documented JSON API if implementing directory ingestion.
