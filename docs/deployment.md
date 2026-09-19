# Automatic deployment on Singa

GitHub runs `.github/workflows/tests.yml` on pushes and pull requests. A user
systemd timer on Singa polls `main` every minute (two minutes after boot) and
requires a successful **push** run of that workflow for the exact commit. PR,
fork, skipped, pending and failed results cannot authorize a deployment. An API
or Git failure leaves production unchanged. No inbound SSH or webhook endpoint
is needed; the machine makes outbound GitHub requests.

People with permission to push to `main` can now change production application
code. Review main changes accordingly; use branch protection and required tests
if you want reviews enforced. Production application credentials stay on Singa.

## Install / update the host controller

Requirements: Linux with user systemd, Python 3.10+, Git, Docker with Compose
supporting `up --wait` and `pull --policy missing`, and authenticated `gh` with
read access to repository workflow runs. The existing Compose stack must be
running, with its `.env` in the source checkout. Enable user lingering with
`loginctl enable-linger` so the timer runs after logout and boot.

From the production checkout, after committing and pushing its changes:

```sh
python3 scripts/install-deploy.py
systemctl --user start rep0rter-deploy.service
```

The installer records the current commit as a minimum baseline, preventing
accidental downgrade to older remote code. It discovers the live worker's data
mount and keeps using that absolute path, along with the original `.env`. Keep
both locations available. The developer checkout is never pulled, reset or
cleaned by automation. Existing installer configuration is preserved on updates.

The controller is copied to `~/.local/share/rep0rter-deploy/deploy.py`, outside
the release checkouts. Changes to the controller or timer need an explicit
reinstall on the host; application updates deploy automatically. Do not reinstall
during an active deployment. Configuration, logs, and state do not require
sharing production credentials with collaborators or putting them in GitHub.

## Deployment sequence

1. Fetch main into a separate repository and verify its GitHub test result.
2. Create a detached release checkout and build a commit-tagged image while
   production continues running. Save the running configuration with actual
   image IDs for rollback. Fetch main and check CI again after building to skip
   commits superseded during the build.
3. Take a verified SQLite backup using the current worker. Journal the pending
   deployment, stop database writers, and regenerate the static site with the
   new image. This command does not collect or send Telegram messages.
4. Start Compose and wait for worker, accounts and web healthchecks, plus the
   maintenance process. Only then record the deployed SHA. The existing worker
   healthcheck includes collection freshness, so startup can take several minutes.
5. If switching fails, rebuild the site and restart services using the previous
   configuration and pinned images. A pending journal also triggers recovery on
   the next invocation after a crash/reboot. The failed SHA is held until a new
   commit or an explicit retry; rollback failures retain the recovery journal.

The website remains available during builds and site generation; restarting web
can briefly interrupt requests and login is unavailable while writers stop.
Rollback restores application code, **not database contents**. Schema migrations
must remain backward compatible; destructive migrations require a separate
maintenance procedure. Backups are retained for operator-led data recovery so an
automatic rollback cannot discard newer posts or withdrawal rules. A host/disk
failure can also prevent rollback; inspect the journal if recovery fails.

The private state directory contains resolved Compose snapshots, including
environment secrets (directory mode 0700, snapshots mode 0600). Never commit or
share these files. `.dockerignore` excludes environment files and production
data from image build contexts. Releases and retained image tags are kept for
recovery; monitor disk space and remove obsolete releases/images only after
checking `current.json`, `previous_config`, and any pending journal. Do not prune
images or release paths used by those files.

## Inspect, pause and retry

```sh
systemctl --user status rep0rter-deploy.timer
journalctl --user -u rep0rter-deploy.service -n 80 --no-pager
cat ~/.local/share/rep0rter-deploy/state.json  # SHAs and paths, no environment secrets

# Pause future deploys; any in-progress deployment continues to completion.
systemctl --user disable --now rep0rter-deploy.timer

# Preview eligibility without building or switching production.
python3 ~/.local/share/rep0rter-deploy/deploy.py \
  --config ~/.local/share/rep0rter-deploy/config.json --check

# Retry a failed current main commit, still requiring passing GitHub tests.
python3 ~/.local/share/rep0rter-deploy/deploy.py \
  --config ~/.local/share/rep0rter-deploy/config.json --retry

systemctl --user enable --now rep0rter-deploy.timer
```

## Apply host environment changes

After editing the production `.env`, apply it through the installed controller:

```sh
python3 ~/.local/share/rep0rter-deploy/deploy.py \
  --config ~/.local/share/rep0rter-deploy/config.json --redeploy
```

This redeploys current remote `main` even when that commit is already running.
It still requires passing push tests, checks for newer commits during the build,
backs up the database, rebuilds the site, and waits for service health. Environment
values are resolved again from the host `.env`; rollback keeps the previous
running configuration. A held failed commit also requires `--retry`.
Use `--redeploy --check` to preview eligibility without changing production.

Manual `--redeploy` and `--retry` use the same lock as the timer and exit 75
without making changes when another deployment is active. Wait for it to finish
and rerun the command. Reinstall the host controller after pulling changes that
introduce this option, following the installation instructions above.

Do not apply environment changes using `docker compose up` from the development
checkout. On 2026-09-19, a concurrent `up --build --no-deps worker` replaced a
container while the controller was waiting for health, causing `No such container`
and an automatic rollback. Raw Docker commands bypass the controller's advisory
lock; all production updates must use this deployment entry point.

For an intentional rollback after a successful deployment, revert the offending
commit on `main` and push; the revert follows the same tests and deployment path.
Do not run ad-hoc `docker compose up` from the developer checkout while the
timer is enabled. Host deployment outcomes are in the systemd journal; GitHub's
green tests indicate CI success and are not themselves proof of deployment.

References: [GitHub workflow runs API](https://docs.github.com/en/rest/actions/workflow-runs)
and [Compose startup health waits](https://docs.docker.com/reference/cli/docker/compose/up/).
