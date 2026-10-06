# voice-vault

Small, security-conscious tool: imports Plaud voice recordings into a
PostgreSQL database on the owner's own server and then deletes them at Plaud,
so conversation content stays at Plaud as briefly as possible. Processing
happens later with Claude, not with Plaud. Built on the Zenplate framework
(layout, deploy pipeline, rules in `.claude/`).

Requirements: `requirements/`. Decisions: `DECISIONS.md` (D-001 to D-004).

## How a cycle works (every `IMPORT_INTERVAL_MINUTES`, default 10)

1. Check the Plaud token. Under `TOKEN_WARN_DAYS` days left: warning in the
   log and, if SMTP is set, one mail per day. Expired or rejected (HTTP 401):
   the cycle stops and nothing is deleted.
2. Import every recording still at Plaud whose transcript and summary Plaud
   reports as finished. Segments (speaker, start/end in ms, text), summary
   and metadata go to `recordings` / `segments`. The rows are read back and
   compared by content hash and segment count (verified import). A changed
   summary or transcript is re-imported.
3. Move to the Plaud trash only if: Plaud reports both tasks done, the
   recording ended at least `MIN_AGE_MINUTES` ago, the import is verified
   and the content was unchanged for `STABILITY_MINUTES`. Otherwise it is
   checked again next cycle.
4. Delete permanently `PERMANENT_DELETE_AFTER_HOURS` after trashing, only
   for a verified import.

`PLAUD_DELETE_ENABLED=false` (default) is shadow mode: steps 3 and 4 only log
`SHADOW would ...`. Doubt always means no deletion.

## Before switching deletion on

1. Put `PLAUD_TOKEN` into `.env` on the server (Local Storage key `tokenstr`
   at web.plaud.ai). Never commit it or paste it into a chat.
2. Run the live check with a throwaway test recording:
   `docker compose run --rm app python plaud_live_check.py` (read-only), then
   `--trash <id>` and `--delete <id>`. This settles whether `DELETE /file/`
   only works on trashed recordings and which `task_status` values occur
   (open questions in REQ-001).
3. Decide the backup. After permanent deletion the database holds the only
   copy. The built-in `pg_dump` runs every `BACKUP_INTERVAL` (default 4h,
   7 days kept) into `volumes/backups/`. Decide whether an off-server copy is
   needed (D-004).
4. Set `PLAUD_DELETE_ENABLED=true` and redeploy.

## Notes

- Plaud tokens may live only about 30 days (not 300). The expiry is read from
  the JWT. There is no refresh: sign in again at web.plaud.ai and update
  `PLAUD_TOKEN`.
- `DATABASE_URL` needs the driver prefix `postgresql+psycopg://`.
- Web UI (REQ-002): loopback-bound port `127.0.0.1:${PORTS_PREFIX}010`; the
  host's `webinterfaces` / `ssh-tunnels` scripts pick it up. Pick a
  `PORTS_PREFIX` that is unique on the server and on the client.
- `STORE_AUDIO` is reserved; audio is not downloaded or stored.
- While Plaud keeps a recording (shadow mode, or waiting for stability) every
  cycle re-reads its detail, transcript and summary to detect changes.

## Operations

```bash
./redeploy.sh                      # tests -> build -> deploy -> verify
docker compose logs -f app         # cycle log, look for "Cycle start/end"
docker compose ps
ls -lh volumes/backups/
cd app && pytest                   # tests + ruff
```

See `.claude/operations.md` and `SETUP.md` for the deploy pipeline.
