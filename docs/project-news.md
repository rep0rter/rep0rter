# Project owners and automatic news

Project owners can sign in with Google at `/projects`, manage their own news
templates, and publish updates without an editor. `/submit` also supports one-off
project announcements. Posts appear in the existing website and RSS feeds and
are labeled as owner submissions. They do not create Telegram delivery jobs.

## Enable Google login

1. In Google Cloud, configure the OAuth consent screen and create an OAuth client
   of type **Web application**. If the consent screen is in testing mode, add the
   accounts that will test login.
2. Register this exact authorized redirect URI for the production site:
   `https://rep0rter.observe.tw/auth/google/callback`.
3. Set these values in the git-ignored `.env` file:

   ```dotenv
   REP0RTER_SITE_URL=https://rep0rter.observe.tw
   REP0RTER_GOOGLE_CLIENT_ID=your-client-id
   REP0RTER_GOOGLE_CLIENT_SECRET=your-client-secret
   REP0RTER_WEB_SECRET_KEY=your-random-session-secret
   ```

   Generate the session secret with
   `python -c 'import secrets; print(secrets.token_urlsafe(48))'`.
   Keep it consistent across accounts-service processes and restarts.
4. On Singa, apply the environment through the controller's
   [`--redeploy` command](deployment.md#apply-host-environment-changes), which
   recreates services and rebuilds the site under the deployment lock.
   Only on a new host without automatic deployment, run
   `docker compose up -d --build` followed by
   `docker compose exec worker python -m rep0rter build-site`.
   The `accounts` service handles `/auth/*`, `/projects`, and `/submit`
   behind Caddy. The worker handles scheduled project updates as part of its
   existing hourly cycle.
5. Visit `/projects`, sign in, preview a template, and save the project settings.

See Google's [OpenID Connect setup](https://developers.google.com/identity/openid-connect/openid-connect)
and [web application OAuth instructions](https://developers.google.com/identity/protocols/oauth2/web-server).
The implementation uses [Authlib's Flask OpenID Connect client](https://docs.authlib.org/en/latest/oauth2/client/web/flask.html).

### Reusing another project's Google client

You can reuse an existing **Web application** OAuth client. In
[Google Auth Platform → Clients](https://console.cloud.google.com/auth/clients),
select the Cloud project and the client used by the existing application. Under
**Authorized redirect URIs**, add
`https://rep0rter.observe.tw/auth/google/callback` and save. Keep every existing
redirect URI so the other application keeps working. Google requires this exact
URI to be registered; otherwise login fails with `redirect_uri_mismatch`.

For the local `urtube` project, copy only these credentials into rep0rter's private
`.env`, without printing or committing their values:

| urtube setting | rep0rter setting |
| --- | --- |
| `GOOGLE_LOGIN_CLIENT_ID` | `REP0RTER_GOOGLE_CLIENT_ID` |
| `GOOGLE_LOGIN_CLIENT_SECRET` | `REP0RTER_GOOGLE_CLIENT_SECRET` |

Generate a separate `REP0RTER_WEB_SECRET_KEY` for rep0rter. Do not copy the other
application's session keys, redirect URI, users, or data. Reusing its client also
reuses that Cloud project's OAuth branding. If you want independent client
credentials, click **Create client → Web application**, name it `rep0rter`, and
register the same callback above. Separate consent-screen branding requires a
separate Cloud project. Download and securely save the new client credentials
when creating them; Google may not show the client secret again later.

After changing production environment settings on Singa, reload them through
the controller so services and the site are updated under the deployment lock:

```sh
python3 ~/.local/share/rep0rter-deploy/deploy.py \
  --config ~/.local/share/rep0rter-deploy/config.json --redeploy
```

Changing a registered Google callback alone does not require restarting the app.
Allow time for the Console change to propagate, then try signing in again.

All three login values are required. Without them, the feed keeps working and
the login page explains that posting is not available. Never put credentials in
templates, static files, or a git commit. Public deployments require an HTTPS
origin without a path prefix. Callback URLs come from configuration, not request
headers. Existing automations continue running if login is disabled; pause each
project before disabling credentials if you also want to stop publication.

## Owner workflow

- Choose a public GitHub repository (`owner/repository`) or a public HTTPS RSS
  2.0 feed you maintain. GitHub automation watches published releases, excluding
  drafts and pre-releases; it does not watch every commit, issue or pull request.
- Write a headline and body template using `$project`, `$title`, `$summary`, and
  `$url`. `$$` inserts a literal dollar sign. Templates substitute plain text;
  they cannot execute code or Jinja expressions.
- Preview using a sample update. Preview does not save settings or publish.
- Choose hourly, six-hourly or daily checks, confirm authorization, and enable
  automatic publishing. These are minimum intervals, processed when the worker
  next runs; keep the worker interval at one hour or less.
- Uncheck automatic publishing and save to pause. Editing settings or pausing
  during a source fetch prevents that fetched update from using stale settings.
- Use **Write a one-off post** for a manual announcement.

Example headline:

```text
$project: $title
```

Example body:

```text
News from $project

$summary

Read the update and join us: $url
```

Google verifies the account; project ownership and authority over the source are
self-declared. The owner chooses a public display name. Google email addresses
and OAuth tokens are not persisted or published. Stable Google subject IDs are
stored privately, separate from public source author IDs. Login sessions expire
after 12 hours and are revoked server-side on logout. POST requests require CSRF
tokens. Only the owning account can view or edit a project's settings.

## Publishing behavior and limits

Only source updates timestamped after enabling (or resuming) automation are
published. Old entries are not backfilled. Changing sources resets this cutoff.
GitHub checks read the latest 30 releases; RSS checks inspect up to 100 items.
Updates that disappear from a rolling upstream feed before a successful check
cannot be recovered. RSS entries need a link and a timezone-bearing `pubDate`.
The selected language must match the source/template language. Other editions
follow the existing translation process and display pending notices as needed.

Each account can manage ten projects and publish five posts per rolling 24-hour
period, including manual posts. Headline and body limits are 160 and 3,000
characters after substitution. Errors appear in the owner's project list and
retry on a later check; shorten an oversized template or source description to
resolve length errors. The existing exclusion and withdrawal policy also applies
to these posts, using source `project` and container `project:owners`.

The worker checks at most 20 due projects per cycle. Fetches have bounded reads,
timeouts, and redirect counts. RSS URLs must use HTTPS on port 443 and resolve
only to public addresses. Each connection pins the resolved address and verifies
the original TLS hostname, including after redirects.

Source-item IDs prevent duplicate automatic posts. Manual forms also carry
signed submission IDs so retrying a request does not create another post. Posts
and their ownership records commit atomically before site generation. A failed
manual site build shows a saved-but-pending message; resubmitting retries the
build, and the next normal worker run also rebuilds the feed. Do not delete a
saved post just because the static-site refresh failed.

## Local development

Register `http://localhost:8000/auth/google/callback` as an additional redirect
URI, set `REP0RTER_SITE_URL=http://localhost:8000`, and provide the three Google
settings above. Then:

```sh
pip install -r requirements-dev.txt
python -m rep0rter build-site
python -m rep0rter serve --port 8000
```

Open `http://localhost:8000/projects`. The local server also serves the generated
feed. Run `python -m rep0rter loop --interval 3600` separately for automation.
Production uses Gunicorn through Compose, not Flask's development server.

Offline tests validate signed Google ID tokens with local test keys, request
forgery defenses, account isolation, source fetching and duplicate-free
publication. A real Google browser login requires your registered client and
must be checked after deployment; no credentials are included with the project.

## Community stories and hashtags

Choose **Write a story** in the site header, or visit `/write`. Google sign-in
returns to the writer (including a tag selected from a timeline). The existing
Google client and callback above are reused; no additional scopes are needed.
The writer supports an English headline and update, optional project name,
participation details, up to five evidence links, original text in any language,
and the date the work happened. Preview does not publish. Published stories are
marked **Self-reported**, including their downloadable English cards. Stories
without evidence explicitly say so. Other editions retain the existing honest
translation fallback; submitting does not automatically translate the story.

Add up to eight hashtags separated by spaces or commas. Tags are Unicode-aware,
normalized for width and case, and deduplicated. Letters, numbers, underscores,
and hyphens are supported (48 characters maximum, at least one letter). Hashtags
in story text are also discovered; URL fragments and numeric issue references
are excluded. Existing Slack channel names become tags when they meet these
rules, so their historical stories are immediately discoverable.

Each tag links to `/tags/<tag>/index.html` and the corresponding language pages.
Timelines include all published, allowed stories, even outside the homepage's
300-post window, sorted by their original event date, newest first. Source pages
also use this dated timeline presentation. The existing date, author, source and
sort filters work within each timeline. The main feed remains ordered by
publication time. Tag counts appear in the discovery sidebar, and RSS includes
hashtags as categories.

Stories share the existing five-publications-per-account daily limit and the
same transactional, account-bound retry protection as project submissions.
They do not send Telegram messages. Exclusions and emergency withdrawal scrub
stories, tag discovery and timeline headings from cached generations; rebuilding
removes empty tag pages. No database migration or extra service is required.
Caddy sends `/write` to the existing accounts service.
