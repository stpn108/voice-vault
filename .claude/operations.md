# Operations

Two audiences. The owner never needs a terminal; everything in the first
half happens on GitHub. The second half is for the mentor on the server.

---

## For the owner (GitHub only)

### Release a change

1. Open the pull request Claude sent you. Read "What changes for users".
2. Check the status line above the Merge button: **Tests ✓** green means
   the automatic tests passed. Red: do not merge; tell Claude.
3. Press **Merge pull request**, then **Confirm merge**.
4. Wait a few minutes. The pipeline writes a comment on the same pull
   request (you also get an e-mail):
   - **✅ Deployed vX.Y** — the change is live.
   - **❌ Deployment failed** — nothing changed on the server, the old
     version keeps running. The comment contains the failing log. Copy it
     and paste it to Claude.
   No comment after 10 minutes: the pipeline did not start. Tell the mentor.

### See what is live

The newest pull request with a **✅ Deployed** comment. Or: repository →
**Actions** → latest green *Deploy* run; its log prints
`Deploying <commit> v<version>`.

### Something looks wrong in the product

Tell Claude what you did, what you expected, what happened, and when.
Claude reads the logs. If the product is down and Claude cannot fix it
from the code, Claude will tell you to contact the mentor.

### Request a change

Describe it to Claude in your own words. Claude writes a requirement, reads
it back, and starts only after your yes. See `.claude/requirements.md`.

### Never

- Never press "Merge" on a red PR.
- Never edit files on GitHub directly; go through Claude.
- Never paste the contents of `.env` or any password into a chat.

---

## For the mentor (terminal on the server)

Run in the deployment directory (`DEPLOY_DIR`).

### Deploy by hand (pipeline broken, or hotfix)

```bash
git pull --ff-only origin main && ./redeploy.sh
```

Tests, build, restart, verify. Aborts at the first failure and leaves the
running app untouched. `./merge-to-main.sh` merges a branch to `main` from
the terminal and waits for the version bump; only for `developer` mode.

### Is the right version running?

```bash
./version.sh
```

### Logs, status, restart

```bash
docker compose ps                    # every service running / healthy?
docker compose logs -f app           # live, Ctrl+C to stop
docker compose logs --tail=200 app
docker compose restart app
```

### Something is down

1. `docker compose ps` — every service should say `running` / `healthy`.
2. `docker compose logs --tail=100 app` — the last lines usually name the cause.
3. `docker compose restart app`.
4. Still down: `./redeploy.sh` (rebuilds from the current code).
5. Still down: check the runner (`Actions → Runners` must show it online),
   disk space (`df -h`), and the database (`docker compose logs db`).

### Backups

The `db-backup` service dumps the database every `BACKUP_INTERVAL`
(default 4h) into `volumes/backups/` and keeps `BACKUP_RETENTION_DAYS`
(default 7) days.

```bash
ls -lh volumes/backups/
docker compose logs db-backup
```

#### Restore

Replaces the live database. Stop the app first.

```bash
docker compose stop app
gunzip -c volumes/backups/backup_YYYY-MM-DD-HH-MM-SS.sql.gz \
  | docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
docker compose start app
```

Copy `volumes/backups/` off the server regularly; the service protects
against mistakes, not against losing the server.

### Runner and pipeline

- Runner status: Repository → Settings → Actions → Runners (label `deploy`).
- Runner service on the server: `sudo ./svc.sh status` in the runner directory.
- `DEPLOY_DIR`: Repository → Settings → Variables → Actions.
- Branch rules on `main`: block force pushes and deletions only (see
  `SETUP.md` §1.4 for why required checks must stay off).

### Environment (.env)

Secrets and settings live in `.env` next to `docker-compose.yml`. Never
committed. `.env.example` lists every key. After changing `.env`:
`docker compose up -d`.
