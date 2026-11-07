# Production deployment

- Production runs natively on sky-server. Read `docs/sky-server.md`. Website,
  accounts, SQLite, publication, reporting and backups use `/srv/rep0rter` and
  user systemd services. AI uses the existing gateway with `sky-fast`; its
  engines stay on sky-mini / skyhong-blog. Never enable Containers.
- Push through a pull request to `main`, require passing GitHub **Offline tests**
  for that exact main commit, then use `scripts/host-deploy.py prepare` and
  `activate` from a clean checkout on sky-server. Verify the public route, health
  record and actual reporting cycle; green CI alone is not deployment success.
- Keep legacy GitHub reporting disabled with `REP0RTER_REPORTING_HOST=sky-server`.
  Keep the old Worker runner and cron disabled. Cloudflare durable data is a
  retained pre-cutover recovery copy; never resume its writers or restore it over
  newer host accounts, exclusions, posts or outboxes without reconciliation.
  `docs/cloudflare.md` documents the retired runtime for recovery reference.
- The former Singa services and deployment timer must remain stopped. Its
  recovery releases are managed by
  `~/.local/share/rep0rter-deploy/deploy.py`. Read `docs/deployment.md` before
  changing production services.
- Never run ad-hoc `docker compose up`, `down`, `restart`, `stop`, container
  removal, or mutating `docker exec` against production during a deployment.
  Direct Compose commands bypass the deployment lock and can destroy containers
  that the controller is waiting on.
- To apply `.env` changes during an explicitly requested Singa recovery, use its controller with
  `--redeploy` as documented. It shares the timer's lock, backup, health checks,
  and rollback. Exit 75 means busy: wait for completion and retry the command.
- Do not edit private deployment snapshots or state to force an update. Do not
  print or commit environment credentials or resolved Compose snapshots.
- After completing code changes, create an appropriate git commit and include
  the commit ID in the final handoff. If committing is unsafe or blocked,
  explain why no commit ID is available.
