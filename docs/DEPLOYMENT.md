# Deployment (CI/CD)

This project deploys via a GitHub Actions **self-hosted runner** registered
on an on-premises server (Rocky Linux). The runner polls GitHub for push
events, so no inbound port needs to be open on the deployment server.

## Trigger condition

- `.github/workflows/deploy.yml` runs only on push to the `main` branch.
- Deploys to the same branch are serialized via `concurrency`, so a new
  deploy never overlaps with one still in progress.

## Deployment flow

1. `actions/checkout` - checks out the latest `main` code into the runner's workspace
2. Runs `scripts/deploy.sh`:
   1. Back up the currently running `backend`/`frontend` images with a `:previous` tag
   2. `docker compose build` - build new images
   3. `docker compose up -d` - start new containers
   4. `scripts/healthcheck.sh` - retry `GET /health` on the backend, up to 10 times, 3s apart
   5. If build, startup, or health check fails, automatic rollback runs
3. The deployment result is written to the GitHub Actions step summary

## Rollback condition and meaning

- **Rollback condition**: `docker compose build` fails, `docker compose up -d`
  fails, or the new containers fail the `/health` check within the retry budget.
- **What rollback means**: the rollback in `scripts/deploy.sh` is a
  **deployment rollback** - it reverts containers to the docker image backed
  up as `:previous` right before the build. It is NOT a source code rollback
  like `git revert`. The commit history on `main` is left untouched.
- If the rollback succeeds (previous image restarted and passing the health
  check), the workflow still reports as failed, but the service keeps
  running on the last known-good version.

## When manual intervention is required

Automation cannot recover in these cases, so someone must log into the
runner server directly:

- **The very first deploy fails**: no `:previous` image exists yet to roll
  back to (`scripts/deploy.sh` exits with code `2`).
- **Restart or health check still fails after rollback**: likely an issue
  with the previous image itself (e.g. incompatible DB schema) or an
  infrastructure-level problem (disk space, port conflict, etc.) (exit code `2`).
- In these cases, check status on the server with `docker ps`,
  `docker compose -p semantic-router logs`, `docker image ls | grep semantic-router`,
  and restart the previous image manually or investigate further as needed.

## Secrets handling

- No secrets are hardcoded in the workflow file or the deployment scripts.
- Sensitive config (DB credentials, `SERVER_IP`, etc.) is provided via a
  `.env` file pre-placed on the runner server (gitignored, never committed
  to the repo) and injected through `env_file` in `docker-compose.yml`.
- The workflow never reads or logs the contents of that `.env` file.

## Running locally / manually

You can run the deployment script directly, the same way the runner does,
to validate it locally.

```bash
chmod +x scripts/deploy.sh scripts/healthcheck.sh
./scripts/deploy.sh
```

To run just the health check:

```bash
./scripts/healthcheck.sh http://localhost:8000/health 10 3
```
