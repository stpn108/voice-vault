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
   recording ended at least `MIN_AGE_MINUTES` ago (default 12 hours) and has a known duration, the import is verified
   and the content was unchanged for `STABILITY_MINUTES`. Otherwise it is
   checked again next cycle.
4. Delete permanently `PERMANENT_DELETE_AFTER_HOURS` after trashing, only
   for a verified import.

`PLAUD_DELETE_ENABLED=false` (default) is shadow mode: steps 3 and 4 only log
`SHADOW would ...`. Doubt always means no deletion.

## Before switching deletion on

1. Put the refresh token into `.env` on the server: `PLAUD_REFRESH_TOKEN`
   (cookie `pld_urt`), from Firefox dev tools on web.plaud.ai under Storage,
   Cookies. `PLAUD_TOKEN` (cookie `pld_ut`) is optional and short-lived; the app
   fetches its own access token. Never commit them or paste them into a
   chat. Then `docker compose up -d app`. From then on the app renews the
   access token itself and stores the current pair in the database
   (table `plaud_sessions`); `.env` is only the seed.
2. Run the live check with a throwaway test recording:
   `docker compose run --rm app python plaud_live_check.py` (read-only), then
   `--trash <id>` and `--delete <id>`. This settles whether `DELETE /file/`
   only works on trashed recordings and which `task_status` values occur
   (open questions in REQ-001).
3. Decide the backup. After permanent deletion the database holds the only
   copy. Ofelia (callisto-services) runs `pg_dump` on `BACKUP_SCHEDULE`
   (default every 4 hours, 7 days kept) into `volumes/backups/`. The job is
   named `backup-<COMPOSE_PROJECT_NAME>`; check that a dump file appears. Decide whether an off-server copy is
   needed (D-004).
4. Set `PLAUD_DELETE_ENABLED=true` and redeploy.

## Notes

- Token renewal (REQ-004, D-005): the access token is renewed before it
  expires and again after any HTTP 401, with `POST /auth/refresh-user-token`.
  Plaud may rotate the refresh token on every renewal, so the pair is kept in
  the database. Do not log out of web.plaud.ai in the browser session you took
  the tokens from; that probably invalidates the refresh token. If the
  refresh token dies (log: "token refresh" error, mail if SMTP is set), paste
  a new refresh token into `.env`: a changed value replaces the stored pair.
- The database dump (`volumes/backups/`) contains the token pair. Protect the
  backup directory like `.env`.
- `DATABASE_URL` needs the driver prefix `postgresql+psycopg://`.
- Web UI (REQ-002, D-008): service `web`, loopback-bound port
  `127.0.0.1:${PORTS_PREFIX}010`; the host's `webinterfaces` / `ssh-tunnels`
  scripts pick it up, open `http://localhost:${PORTS_PREFIX}010`. Pick a
  `PORTS_PREFIX` that is unique on the server and on the client. The UI has no
  login (SSH tunnel is the access control). It lists recordings (newest first,
  50 per page), searches title, summary and transcript, shows summary and
  transcript, and discards recordings one by one or everything older than N
  days. Discarding removes the content here for good and never imports the
  recording again. At Plaud it is deleted by the normal cycle (trash after the
  minimum age, permanent after the wait), without the verification and
  stability conditions; with `PLAUD_DELETE_ENABLED=false` that is only logged.
  `web` has no Plaud credentials.
- `STORE_AUDIO` is reserved; audio is not downloaded or stored.
- While Plaud keeps a recording (shadow mode, or waiting for stability) every
  cycle re-reads its detail, transcript and summary to detect changes.

## Claude access over MCP (REQ-003, D-011)

Opt-in. The `mcp` service gives Claude read-only access to the recordings: search
(`list_recordings`) and read (`get_recording`, summary plus transcript with speaker and time).
It cannot change or delete anything.

1. In `.env`: `COMPOSE_PROFILES=mcp`, `MCP_TOKENS=$(openssl rand -hex 32)` and
   `MCP_ALLOWED_HOSTS=<public host name of your reverse proxy>`. Without a token of at
   least 32 characters the service refuses to start.
2. `./redeploy.sh` starts and health-checks it. It listens on
   `127.0.0.1:${PORTS_PREFIX}020`, separate from the UI port.
3. Reverse proxy: terminate TLS, forward only `/mcp` to that port, pass the `Host`
   header (`proxy_set_header Host $host;` in nginx), do not buffer responses, and add a
   rate limit. Do not expose `/healthz`.
4. Claude Code:
   `claude mcp add --transport http voice-vault https://<host>/mcp --header "Authorization: Bearer <token>"`.
   claude.ai: custom connector with the URL and the same header under "Request headers"
   (beta, not available to every organisation; otherwise claude.ai needs OAuth, not built yet).
5. Rotate the token: put the new token next to the old one in `MCP_TOKENS`
   (comma separated), redeploy, update Claude, then remove the old one.

Anyone with the token can read every stored conversation. Keep it like a password. What
Claude reads goes to Anthropic for processing; that is the point of the tool, but it
means the recordings are no longer only on your server once you ask Claude about them.
Recording text is untrusted: a spoken sentence like "ignore previous instructions" must
not be obeyed, which is why the tools are read-only.

## Operations

```bash
./redeploy.sh                      # tests -> build -> deploy -> verify
docker compose logs -f app         # cycle log, look for "Cycle start/end"
docker compose ps
ls -lh volumes/backups/
cd app && pytest                   # tests + ruff
```

See `.claude/operations.md` and `SETUP.md` for the deploy pipeline.
