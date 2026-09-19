# Cloudflare static hosting

Step 1 of [#24](https://github.com/rep0rter/rep0rter/issues/24): the generated
site is served from Workers Static Assets instead of the `web` (Caddy) container.
The pipeline and the `accounts` app are unchanged and still run on the host.

Node tooling is scoped to this directory; the rest of the repository stays
Python-only. Versions are pinned in `package.json`, so install with `npm ci`
rather than letting `npx` fetch whatever is current.

## What the Worker does

`worker.ts` reproduces `Caddyfile`: `/auth/*`, `/submit`, `/projects` and
`/projects/*` are forwarded to `APP_ORIGIN`, and everything else is served from
the uploaded site. `_headers` reproduces the cache and content-type rules.

## Three settings that are not optional

- `html_handling: "none"`. Every other mode answers `/index.ja.html` with a 307
  to an extension-less URL, which would break shared links, RSS subscribers and
  every link already posted to Telegram.
- Each `_headers` rule that overrides an inherited header drops it with `!`
  first. Rules accumulate rather than replace, so without it an asset is served
  as `Cache-Control: no-cache, public, max-age=31536000, immutable`.
- `publish.sh` runs the pinned wrangler from `node_modules`. A deploy must not
  depend on whatever version `npx` resolves that day.

`tests/test_cloudflare_deploy.py` guards the first two and checks that the
forwarded paths still match the `Caddyfile` matcher.

## Working here

```sh
cd deploy/cloudflare
npm ci
npm run typecheck     # tsc --noEmit
npm run types         # regenerate binding types after editing wrangler.jsonc
```

The `Env` interface in `worker.ts` types the `ASSETS` binding and `APP_ORIGIN`.
A mistyped binding is otherwise a runtime failure in production.

## Publishing

```sh
REP0RTER_DATA_DIR=/data deploy/cloudflare/publish.sh
```

Needs `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` in the environment.

## Verifying before the cutover

Run the Worker against a locally built site, with `APP_ORIGIN` pointed away from
production so the check sends no traffic to the live app:

```sh
./node_modules/.bin/wrangler dev --assets "${REP0RTER_DATA_DIR:-./data}/site" \
  --local --var APP_ORIGIN:http://127.0.0.1:9999
```

Every published URL must answer 200 and never 3xx.
