# Production deployment

- Production runs on native Cloudflare Workers. Read `docs/cloudflare.md`.
  Push to `main`, require passing GitHub **Offline tests** for that exact commit,
  and run `python3 cloudflare/deploy.py --production` from its clean checkout.
  Verify the public route, health record, and reporting cycle; green CI alone
  is not deployment success. Never enable Containers for this deployment.
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
