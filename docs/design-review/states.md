# Design review state inventory

Every review round captures exactly these states. Each state is captured at two
sizes:

| Size | Viewport | Device scale |
|---|---|---|
| `desktop` | 2560 × 1440 (1440p) | 1 |
| `mobile` | 390 × 844 | 2 (touch, mobile UA metrics) |

Screenshots are viewport captures, not full-page captures; long pages have
separate scrolled states. Light appearance unless the state says dark.

Capture only local previews: `scripts/design-review/round.sh <checkout> <port> <out>`
seeds fictional four-language data (`preview.py`), serves the reader site and
Flask account pages on 127.0.0.1, and runs `capture.py`. Form states post to
that local preview only, never to production. The entrance animation is
suppressed (its own "dismissed" flag) except in `home-entrance`.

## Reader site

| ID | Page | State |
|---|---|---|
| `home-en` | Home (English, default edition) | With data, top of page |
| `home-en-feed` | Home | Scrolled to the "Browse by date" timeline |
| `home-en-footer` | Home | Scrolled to the bottom / footer |
| `home-zh-TW` | Home (繁體中文) | With data, top |
| `home-ja` | Home (日本語) | With data, top |
| `home-ko` | Home (한국어) | With data, top |
| `home-en-dark` | Home | Dark appearance, top |
| `home-en-dark-feed` | Home | Dark appearance, timeline |
| `home-entrance` | Home | First-visit brand entrance in progress (loading) |
| `home-language-loading` | Home | Language switch to 日本語 in flight (request stalled) |
| `home-language-failed` | Home | Language switch timed out; error status shown |
| `home-search-results` | Home | Search "map" with matches |
| `home-search-no-results` | Home | Search with no matches (empty result) |
| `home-filters-open` | Home | "Filter & sort" panel expanded |
| `home-filters-invalid-range` | Home | Custom date range with start after end (validation error) |
| `home-filter-source` | Home | One source filter applied |
| `menu-language` | Home | Language menu open |
| `menu-appearance` | Home | Appearance menu open |
| `login-dialog` | Home | In-page sign-in dialog open (signed out) |
| `login-dialog-loading` | Home | Sign-in dialog while its content is loading |
| `login-dialog-error` | Home | Sign-in dialog after its content failed to load |
| `home-empty` | Home | Site with no stories at all (empty data, collection delayed) |
| `story-en` | Story detail (English) | With data |
| `story-zh-TW` | Story detail (繁體中文) | With data |
| `story-en-dark` | Story detail | Dark appearance |
| `story-superseded` | Story detail | Older revision superseded by a newer report |
| `story-revision-latest` | Story detail | Latest revision of a self-reported community story, with revision links |
| `story-translation-pending` | Story detail (English) | English translation not stored yet (pending notice) |
| `story-untranslated-ko` | Story detail (한국어) | Edition missing; stored text shown with notice |
| `image-viewer` | Story detail | Report image viewer open |
| `image-viewer-error` | Story detail | Image viewer when the image fails to load |
| `source-timeline` | Source timeline | Stories from one source |
| `tag-timeline` | Hashtag timeline | Stories for `#opendata` |
| `withdrawn` | Withdrawn story | Withdrawal notice |
| `presentation-start` | `/ppt` presentation | Slide 1 |
| `presentation-demo` | `/ppt` presentation | Slide 3 with embedded live demo |

## Account and forms (Flask)

| ID | Page | State |
|---|---|---|
| `sign-in-page` | `/auth/sign-in` | Signed out, English |
| `sign-in-zh-TW` | `/auth/sign-in` | Signed out, 繁體中文 |
| `sign-in-error` | `/auth/sign-in` | Google sign-in failed or cancelled |
| `sign-in-unavailable` | `/auth/sign-in` | Login not configured (HTTP 503) |
| `signed-in-card` | `/auth/sign-in` | Already signed in |
| `write-empty` | `/write` | Before submit, empty form |
| `write-ja` | `/write` | Before submit, 日本語 interface |
| `write-filled` | `/write` | Before submit, filled in, scrolled to actions |
| `write-preview` | `/write` | Preview of the unpublished story |
| `write-error` | `/write` | Server validation failure |
| `write-submitting` | `/write` | Publish request in flight |
| `write-saved-build-failed` | `/write` | Saved, but feed refresh failed (retry) |
| `write-success` | Story detail | Redirected to the newly published story |
| `submit-empty` | `/submit` | Project post, before submit |
| `submit-error` | `/submit` | Server validation failure |
| `submit-submitting` | `/submit` | Publish request in flight |
| `submit-success` | Story detail | Redirected to the published project post |
| `projects-empty` | `/projects` | Automatic project news, no projects yet |
| `projects-preview` | `/projects` | Template preview |
| `projects-error` | `/projects` | Validation failure |
| `projects-saved` | `/projects/<id>` | Saved notice with project list |

## Reference sites

Captured once (`scripts/design-review/competitors.py`) at the same sizes,
read-only: Rest of World, The Markup and Semafor. States where available:
`home`, `home-feed`, `home-footer`, `article`, `search`, `search-no-results`,
`language` (another edition) and `newsletter` (a sign-up form before submit).

## Excluded

- Static-file 404 responses: produced by the production Worker as plain text and
  out of scope for template work.
- Real Google OAuth screens: hosted by Google; the preview never presses the
  Google button.
