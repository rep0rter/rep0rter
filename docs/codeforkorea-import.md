# Import Code for Korea archives

```sh
python -m rep0rter import-korea
```

This command collects Code for Korea's public news and project archive feeds
and adds up to ten unique entries to the configured `REP0RTER_DATA_DIR`
database. It builds the shared website, source filters, permanent story pages,
and localized RSS feeds. Read `site/index.ko.html` or `site/feed.ko.xml` for
original Korean content. English shows the existing translation-pending notice
until translations are available.

Selection uses normalized article URLs and exact text with whitespace normalized.
Public-access and opt-out checks apply. Original dates, post IDs, and RSS GUIDs
survive repeated imports. The page and RSS label archive entries and source
excerpts. A project archive entry's date describes the listing, not its launch.

`--days` bounds source dates (default: 3650); `--limit` bounds new posts per run
(default: 10). Rolling feeds provide only their current entries. The import
creates website posts directly, with no bot delivery jobs. The hourly reporter
keeps its existing selection policy.

To generate summaries, configure `AI_BASE_URL`, `AI_API_KEY`, and `AI_MODEL`
locally and run:

```sh
python -m rep0rter import-korea --summarize
```

This uses the existing four-language writer and its evidence checks. Items held
for review remain unpublished. The default import preserves Korean feed text,
which may be an excerpt. Source links lead to the original articles.

Deployment of code alone does not import archive entries. Run the command in
the target environment with its shared data directory. Coordinate production
imports with the deployment controller described in [deployment.md](deployment.md).
Local imports affect the local site only.
