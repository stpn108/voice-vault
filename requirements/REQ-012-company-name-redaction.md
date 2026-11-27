# REQ-012: The company name is replaced by "[Firma]" before anything is stored

| | |
|---|---|
| **Status** | DRAFT |
| **Date** | 2026-11-27 |
| **Requested by** | Dennis Winter |
| **Implemented in** | — (branch `claude/req-012-company-name-redaction`) |
| **Related decisions** | D-003, D-046, D-062, D-081, D-082 |
| **Supersedes / superseded by** | — |

## Owner ask (verbatim)

The real names are replaced by `[Firma]` here on purpose: this file is in git, and the whole point is that the
name is not stored anywhere (criterion 1).

> Aus einer Sicherheitsperspektive raus, ist es möglich, dass bestimmte Worte vor dem Abspeichern oder bestimmte
> Terms vor dem Abspeichern immer ausgetauscht werden, sodass die nicht in irgendwelchen, also dass sie nicht in
> der Datenbank erscheinen, wie zum Beispiel der Name meiner Firma oder ja, zum Beispiel der Name meiner Firma. Und
> da müsste es halt mehrere Varianten geben, die gefunden werden. Also [Firma], [Firma], so in die Richtung.

> Ja, entwirf REQ-011, ein Platzhalter "[Firma]".

> Und nein, kein Schattenmodus.

## Goal

The name of the owner's company, in all the spellings he lists, never lands in the database. Title, summary and
transcript of a recording carry "[Firma]" in its place from the moment they are stored, and so does everything
Claude writes from them. The list of names lives only in `.env` on the server, never in the database, in git or in
a log. The deletion at Plaud works as before.

## Acceptance criteria

The examples use an invented company, "Mühlental Bank" with the short forms "MTB" and "MTBD" for its unit
"Mühlental Bank Digital". The real names are never written into the repository, the tests included.

1. **The list is in `.env` only.**
   Given `REDACT_TERMS=Mühlental Bank Digital;Mühlental Bank;MTB;MTBD` in `.env`, when `app` starts, then the log
   says `Redaction on terms=4` and nothing else about the terms; no table holds any of them.
   Given `REDACT_TERMS` empty or unset, then nothing is replaced and the log says `Redaction off`.
   An entry shorter than 2 characters stops the start with an error that names its position, not its text.
2. **The import replaces before it stores.**
   Given a recording at Plaud with the title "MTB Quartalsplanung", the summary "Termin bei Mühlental Bank Digital"
   and a segment "die mühlental-bank macht das", when it is imported, then `recordings.title` is
   "[Firma] Quartalsplanung", `recordings.summary` is "Termin bei [Firma]" and `segments.text` is
   "die [Firma] macht das".
3. **Variants are found.**
   Each entry matches as a whole word, regardless of case, with any number of spaces or hyphens (or none) between
   its words, with a trailing "s" or "'s", and with every umlaut also written as two letters or without dots
   (ä/ae/a, ö/oe/o, ü/ue/u, ß/ss). An entry in capitals of up to 5 letters also matches with dots or spaces between
   the letters. The longest entry wins.

   | Text | Stored as |
   |------|-----------|
   | `MUEHLENTAL BANK` | `[Firma]` |
   | `Muhlental-Bank` | `[Firma]` |
   | `Mühlentalbank` | `[Firma]` |
   | `der Mühlental Banks` | `der [Firma]` |
   | `Mühlental Bank Digital` | `[Firma]` (not `[Firma] Digital`) |
   | `M.T.B.` / `M T B` / `mtb` | `[Firma]` |
   | `MTB-Team` | `[Firma]-Team` |
   | `info@muehlental-bank.de` | `info@[Firma].de` |
   | `Mühlental` / `Bank` / `MTBF` | unchanged |

4. **`#secret` is checked on the original title.**
   Given a recording titled "#secret MTB", when the import runs, then it is skipped before any content is fetched,
   as D-046 says.
5. **Verification and deletion work as before.**
   Given the recording of criterion 2, when it is imported, then the content hash is computed from the replaced
   text, the read-back verifies, and the recording is trashed and deleted at Plaud under the usual conditions
   (D-003). Given the next cycle with the same content at Plaud, then it is not imported again.
6. **Claude writes "[Firma]" too.**
   Given the analysis, when Claude calls a write tool with a listed name in any text argument (the fields the
   glossary fixes, D-062, plus the topic name of `add_topic_note` and `wrong` / `right` / `hint` of
   `suggest_spelling`), for example `add_todo` with the title "Rückmeldung an Mühlental Bank", then the stored title
   is "Rückmeldung an [Firma]". The replacement runs before the glossary.
7. **The routine knows the placeholder.**
   `SYSTEM_PROMPT` says that "[Firma]" stands for a name removed on purpose, that Claude keeps it as it is and never
   guesses or writes the name it stands for.
8. **What the owner types is replaced too.** *(proposal, open question 2)*
   Given the owner saves a task title "Call mit MTB", a profile field, a topic note or a topic name in the UI, then
   "[Firma]" is stored. `web` gets `REDACT_TERMS` for that.
9. **Data already stored can be cleaned once.** *(proposal, open question 1)*
   Given stored rows that contain a listed name, when the owner runs
   `docker compose run --rm app python redact_existing.py`, then every match is replaced in `recordings` (title,
   summary), `segments`, `todos` (title, detail), `todo_events` (note, and the values inside `before_json` /
   `after_json`, which stay valid JSON), `topics` (name), `topic_notes`, `person_profiles` (role, current_work,
   next_points, agreements), `person_topic_notes`, `digests` and `text_versions`; glossary terms, aliases and
   suggestions containing a listed name are deleted; a topic whose new name already exists gets " (2)"; the command
   prints the number of changed rows per table, no text, and then runs `VACUUM FULL` on the changed tables so the old
   row versions leave the data files. Run a second time, it changes nothing.

## Out of scope

- Plaud. The name stays in Plaud's transcript and summary until the recording is deleted there.
- Spellings that are not listed. Misheard forms ("Mühlenthal Bang") are added to `REDACT_TERMS` by the owner; there
  is no fuzzy matching.
- Context that points to the company: products, colleagues, places.
- Speaker names.
- The search: a search for a listed name finds nothing; a search for "[Firma]" finds the replaced passages.
- Host log files (`access.log` holds what was typed into the UI search), PostgreSQL's write-ahead log, and the
  backups in `volumes/backups/`: they keep old text until they age out (`BACKUP_RETENTION_DAYS`, 7 days).
- An undo. Once Plaud has deleted the recording, a wrong replacement is permanent.
- A page in the UI for the list: it would put the names into the database.
- A shadow phase (owner, 2026-11-27).

## Success metric

On the server, for each listed name, typed by the owner in `psql` and not saved anywhere:
`select count(*) from segments where text ~* '<name>'` is 0 for recordings imported after the switch, and
`select count(*) from segments where text like '%[Firma]%'` grows.

## Open questions

| Date | Question | Answer |
|------|----------|--------|
| 2026-11-27 | One placeholder, or one per unit? | One: "[Firma]" (owner). |
| 2026-11-27 | Shadow phase before switching on? | No (owner). |
| 2026-11-27 | Titles too, or only summary and transcript? | Titles too (Claude's proposal, criterion 2). |
| 2026-11-27 | The list in `.env`, or under "Verwaltung" with only keyed fingerprints in the database? | `.env` (owner, once the reason was clear). |
| 2026-11-27 | 1. Clean the data already stored (criterion 9)? | open |
| 2026-11-27 | 2. Replace what the owner types in the UI as well (criterion 8)? | open |

## Tests

<Filled in at IMPLEMENTED: test file and test names per criterion.>