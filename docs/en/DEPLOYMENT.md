# Deployment (CI/CD)

> 한국어: [../ko/DEPLOYMENT.md](../ko/DEPLOYMENT.md)

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
   4. `scripts/healthcheck.sh` - retry `GET /health` on the backend, up to 40 times (2 minutes), 3s apart. The backend loads torch and checks the DynamoDB table at startup, which can take longer than 30s
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

## Chat attachment storage and the deploy pipeline

`docker-compose.yml`'s `backend` service bind-mounts a host directory (path
set via `UPLOAD_HOST_DIR` in `.env`, kept out of the repo since it's host-specific)
to `/data/uploads` (container) for chat file attachments. This does not
interact with `scripts/deploy.sh`'s backup/rollback logic:

- The script only tags/restores the **built images** (`semantic-router-backend`,
  `semantic-router-frontend`) as `:previous` — it never touches volumes or
  bind mounts.
- `UPLOAD_HOST_DIR` points at a plain host directory, not a Docker-managed named
  volume (unlike `backend_hf_cache`/`dynamodb_data`). It exists independently
  of any container lifecycle, so both a normal redeploy and an automatic
  rollback mount the exact same host path — uploaded files are unaffected
  either way.
- The directory is created automatically (by Docker on first `up`, or by the
  app itself via `Path.mkdir(parents=True, exist_ok=True)`) if it doesn't
  already exist on the runner, so no manual setup step is required before the
  first deploy after this change, other than setting `UPLOAD_HOST_DIR` in the
  runner's `.env`.
- Inside that mount, files are split by purpose: `uploads/chats/{chat_id}/`
  for chat attachments (implemented) and `uploads/projects/{project_id}/`
  for project reference files (not implemented yet). The volume mount itself
  doesn't need to change when this split changes - it's just a subfolder
  layout inside the same bind-mounted directory.

### Lifecycle rules for uploaded files

| | Chat attachments | Project reference files (planned) |
| --- | --- | --- |
| Before use | Orphaned (not yet attached to a sent message) for 24h → swept and deleted | N/A - confirmed the moment it's uploaded, no orphan state |
| Deletion | Deleted individually by the user, or the whole `uploads/chats/{chat_id}/` directory is removed when the chat room is deleted | Deleted individually via a delete API call (disk file + DB row removed immediately, project usage recalculated), or the whole `uploads/projects/{project_id}/` directory is removed when the project is deleted |

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

## First deploy after the FastAPI + Postgres → Flask + DynamoDB switch

- **Data is not migrated.** The new backend starts with an empty table in the server's DynamoDB Local (`dynamodb_data` volume). The old Postgres data (`postgres_data` volume) is left in place.
- **The old `postgres` container keeps running.** It is no longer in `docker-compose.yml`, so compose only warns about an orphan. After the new version is confirmed healthy, remove it by hand with `docker stop postgres && docker rm postgres` (the volume stays).
- **Automatic rollback does not work for this deploy.** The `:previous` image is the FastAPI image that needs Postgres, which the new `docker-compose.yml` no longer defines, so a rollback cannot pass `/health`. If the deploy fails, check out the previous commit and run `docker compose up -d --build` by hand.
- `UPLOAD_HOST_DIR` is mounted at `/data/uploads` in the container. Old files stay on disk but are not referenced by the new database.
