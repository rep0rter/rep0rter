# Production deployment

- Singa runs production from isolated releases managed by
  `~/.local/share/rep0rter-deploy/deploy.py`. Read `docs/deployment.md` before
  changing production services.
- Push code changes to `main` and let the controller require passing GitHub tests.
  Verify deployment in its state file and systemd journal; green CI alone is not
  deployment success.
- Never run ad-hoc `docker compose up`, `down`, `restart`, `stop`, container
  removal, or mutating `docker exec` against production during a deployment.
  Direct Compose commands bypass the deployment lock and can destroy containers
  that the controller is waiting on.
- To apply production `.env` changes, use the installed controller with
  `--redeploy` as documented. It shares the timer's lock, backup, health checks,
  and rollback. Exit 75 means busy: wait for completion and retry the command.
- Do not edit private deployment snapshots or state to force an update. Do not
  print or commit environment credentials or resolved Compose snapshots.
- After completing code changes, create an appropriate git commit and include
  the commit ID in the final handoff. If committing is unsafe or blocked,
  explain why no commit ID is available.
