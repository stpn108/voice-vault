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
   reports as finished (titles starting with `#secret` are never fetched and stay at Plaud, D-046) and in which at least `MIN_SPEAKERS_NAMED_PERCENT` (default 60)
   of the speakers carry a real name instead of "Speaker 2". Name the speakers in Plaud; until then
   nothing is stored and nothing is deleted (the log says `Waiting for speaker names`). After
   `SPEAKER_NAMING_WAIT_DAYS` (default 7) from the end of the recording it is imported with the names it
   has, and deleted as usual. Segments (speaker, start/end in ms, text), summary
   and metadata go to `recordings` / `segments`. The rows are read back and
   compared by content hash and segment count (verified import). A changed
   summary or transcript is re-imported.
3. Move to the Plaud trash only if: Plaud reports both tasks done, the
   recording ended at least `MIN_AGE_MINUTES` ago (default 1 minute, so right away) and has a known duration, the import is verified
   and the content was unchanged for `STABILITY_MINUTES`. Otherwise it is
   checked again next cycle.
4. Delete permanently `PERMANENT_DELETE_AFTER_HOURS` after trashing (default 0: right after, in the
   same cycle), only for a verified import. With the defaults a recording is gone at Plaud about
   `STABILITY_MINUTES` after it arrived here. There is no restore at Plaud then: the database and
   its backups are the only copy.

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
   `--trash <id>` and `--delete <id>`. This settles whether the permanent delete
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
  refresh token dies (log: "token refresh" error, mail if SMTP is set, reminder in the UI), paste
  a new refresh token under "Verwaltung → Plaud-Zugang" (`/admin/plaud`, no restart, D-066). A changed value
  in `.env` still replaces the stored pair; `PLAUD_REFRESH_TOKEN` there is only needed for the first start.
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

## Tasks, topics and the daily routine (REQ-006, D-013)

The UI (`/todos`, `/topics`, `/digests`) opens with the task board (REQ-008: status as columns, priority as rows, cards you drag; the same tasks as a table under "Tabelle"; the recordings are at `/recordings`) and shows the history
of each topic and the daily overviews. The analysis on the server (next section) gives Claude `list_todos`, `add_todo`,
`update_todo` (status `in_progress`, `done`, `dropped` or `open`; `people` to talk to), `get_person`, `update_person`, `set_person_topic`, `get_glossary`, `suggest_spelling`, `list_topics`, `get_topic`, `add_topic_note`,
`mark_recording_analyzed`, `save_digest` and `list_digests`. Every change is logged with who made
it and can be undone on the task page. Claude can delete nothing. You can delete a topic on its page,
after a confirmation: its notes go, its tasks stay. The task list sorts by priority, due date, age, topic
or title. "Statistik" (`/stats`) charts task flow, open tasks by priority, open tasks per person and conversation hours
(Chart.js 4.4.7, vendored in `app/static/vendor`). The header link "Verwaltung" (`/admin`) collects the admin tools: "Personen" (delete, rename or merge
names, name the unnamed speakers of stored transcripts), "Schreibweisen" (the right spelling of names and terms: suggestions from your corrections, applied to everything Claude writes; transcripts stay as they are) and "Aufräumen". "Zusagen an mich" (formerly "Wartet auf") in the task list holds what other people promised you; a click on a name opens a briefing on that
person (`/person`) with a living profile (role and unit, current work, what to raise next time, a list of at most 15 agreements (REQ-009), one note per shared topic) that the routine keeps up to date and you can correct (all people at a glance, with a search by name, at `/people`, REQ-010); "Report" (`/report`, REQ-013, REQ-014) lists the titles of your tasks for a status update (finished in the last 7 days, in progress, open), or picked from a dropdown what another person promised you, with a button that copies it as text; below it a Mindmap of the person's topics shows which of them probably belong together; it is arranged like the big Mindmap and kept per person. An ✕ on a topic takes it off the person (restore under "Verwaltung → Personen"); a conversation counts for a person's topics only when they spoke at least 20 % of it. A task can name the people you have to talk to: the task page shows the speaker names of its
recording as tags: tap to add, tap again to take off, saved at once; a click on a name in the list filters by that person.

The instructions of the routine are `SYSTEM_PROMPT` in `app/llm_client.py`.

## Analysis on the server (REQ-007, D-072)

With `ANTHROPIC_API_KEY` in `.env` the app runs the routine above itself: every `IMPORT_INTERVAL_MINUTES` it
takes the recordings of the last `ANALYSIS_LOOKBACK_DAYS` days that are not analyzed and not waiting for Plaud,
at most `ANALYSIS_MAX_RECORDINGS` per run, oldest first, and lets Claude (`ANALYSIS_MODEL`, default
`claude-opus-5-5`) work through them with the tools of `mcp_tools.py` and their rules. Without new recordings no request
is sent. Only the `app` service gets the key. Each request writes a row to `llm_traces` (tokens, model, status,
hashes, no content), each run a row to `analysis_runs`. A failed run sends at most one mail a day; a recording that
failed twice within 24 hours waits 24 hours. The cost in dollars is in the Anthropic Console; a workspace with a
spend limit for this key is the cap.

Switching over: pause the claude.ai scheduled task first, then put the key into `.env` and run
`docker compose up -d app` (or deploy). Both at once would work through the same recordings twice.

```sql
-- runs per status per day
SELECT date(started_at), status, count(*) FROM analysis_runs GROUP BY 1, 2 ORDER BY 1 DESC;
-- tokens per day
SELECT date(ts_utc), sum(input_tokens), sum(cache_read_tokens), sum(output_tokens) FROM llm_traces GROUP BY 1 ORDER BY 1 DESC;
```

## Operations

```bash
./redeploy.sh                      # tests -> build -> deploy -> verify
docker compose logs -f app         # cycle log, look for "Cycle start/end"
journalctl -t voice-vault-app-1 --since "2 days ago"   # same log, kept across redeploys (D-065)
docker compose ps
ls -lh volumes/backups/
tail -f /var/log/voice-vault/access.log   # one line per web request (D-070)
tail -f /var/log/voice-vault/error.log    # warnings, errors, tracebacks of web
cd app && pytest                   # tests + ruff
```

### Log files on the host (D-070)

`web` writes two files to `/var/log/${COMPOSE_PROJECT_NAME}/` in addition to stdout
(journald, `docker compose logs`):

- `access.log`: one line per HTTP request, combined-log style plus `xff="…"`, `rt=<seconds>` and
  `svc=web`. The path and the Referer are logged without query string (OAuth codes and state
  travel there); bodies, cookies and Authorization headers are never logged. Health checks
  (`ACCESS_LOG_SKIP`, default `/health,/healthz`) are left out.
- `error.log`: everything at WARNING and above, same format as the console, including tracebacks.

`redeploy.sh` creates the directory (owner = the deploy user, group `adm`, mode 0750) and installs
`/etc/logrotate.d/${COMPOSE_PROJECT_NAME}` from `deploy/logrotate.conf` (daily, 14 days, compressed).
It uses `sudo` only for these two steps and only when something is missing or different. When sudo
needs a password and there is no terminal (the deploy pipeline), it stops and prints the two
commands to run once by hand:

```bash
sudo install -d -m 0750 -o <deploy user> -g adm /var/log/<project>
sed -e 's/<NAME>/<project>/g' -e 's/<USER>/<deploy user>/g' deploy/logrotate.conf \
  | sudo install -m 0644 /dev/stdin /etc/logrotate.d/<project>
```

If the directory is missing or not writable inside a container, the service logs one warning and
runs without the files.

See `.claude/operations.md` and `SETUP.md` for the deploy pipeline.
