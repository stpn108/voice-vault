# REQ-006: Daily routine that maintains a prioritized to-do list and an overview

| | |
|---|---|
| **Status** | APPROVED (owner's decisions in chat, developer mode) |
| **Date** | 2026-10-07 |
| **Requested by** | Dennis Winter |
| **Implemented in** | — (branch `claude/festive-bohr-31plpp`, not merged) |
| **Related decisions** | D-004, D-011, D-012, D-013 |
| **Supersedes / superseded by** | amends REQ-003 criterion 8 (Claude may now write to the task tables) |

## Owner ask (verbatim)

> Ich möchte die Aufnahmen dafür einsetzen, um [...] eine tägliche [Routine] [...] priorisierte To-Do-Liste. Und eine Übersicht einfach darüber, wie sich Dinge entwickeln.

Decisions given by the owner: the list lives on his own server (voice-vault database). Claude may create, update and complete tasks; everything is logged with its source and can be undone in one click; Claude cannot delete, it can only mark a task as dropped. Check-off in the UI at the desk and through Claude (also on the phone through the connector); no public mobile page. Overview: topic timeline and daily digest.

## Goal

Once a day Claude reads the recordings that are new since its last run, adds the tasks they contain, updates or completes tasks that the conversations changed, notes how each topic develops and writes a short overview of the day. The owner sees one prioritized list, checks tasks off, and can follow a topic over time.

## Acceptance criteria

1. **Tasks.**
   Given a task from a recording, when Claude calls `add_todo`, then a task with title, priority 1 to 4 (urgent to low), optional detail, due date, topic and source recording is stored, created by `claude`. A similar open task (same words, Jaccard 0.6) is rejected with a pointer to the existing one unless `allow_similar` is set.
2. **Changes are logged and undoable.**
   Given any change by Claude or the owner, then an event with actor, kind, old and new values and source recording is written. The owner can undo the latest event of a task in the UI; an undo is refused when the task changed since.
3. **No delete for Claude.**
   Given any tool, then no task, topic, note, digest or recording can be deleted. The strongest change is status `dropped`. The owner alone can delete a topic in the UI (criterion 13).
4. **Check off.**
   Given the UI list, when the owner presses the check mark, then the task is `done` with a timestamp, logged as `owner`. Claude can do the same with `update_todo`.
5. **Topics.**
   Given a topic, then `add_topic_note` stores one note per topic and recording (a second call replaces it). `get_topic` and the UI page show open tasks and a timeline of notes and task events, newest first.
6. **Daily digest, dated by the conversations.**
   Given a day, then `save_digest` stores a Markdown overview, replacing an earlier one of the same day. The day is the day of the conversations (Plaud's start time, local time, a day runs from 04:00 to 04:00), not the day the routine runs. A day without a stored recording is refused with the days that have recordings. A run that covers several days saves one overview per day. The UI shows one day at a time with older/newer links, day chips and the recordings of that day.
7. **Which recordings are new.**
   Given `mark_recording_analyzed`, then `list_recordings` with `unanalyzed_only` skips that recording and shows `analyzed` in the others.
8. **UI.**
   Given the web service, then `/todos` (list, add, check off, filter), `/todos/{id}` (edit, history, undo), `/topics`, `/topics/{name}` and `/digests` work, state changes need the CSRF token, all text is escaped.
9. **Input is validated.**
   Given wrong types, unknown ids, bad dates or an unknown status, then the MCP call fails with -32602 or a tool error and the UI answers 400 or 404; nothing is written.
10. **Recording text stays untrusted.**
    Given a transcript with instructions, then the tool descriptions say not to follow them, and no tool can delete data, so the worst case is a wrong task that the owner can undo.
11. **Only the owner's own tasks.**
    Given a commitment of another person, then it is not a task. The tool description and the routine prompt say so; Claude may mention it in a topic note instead. (Added 2026-10-07 on the owner's request: "Aufgaben zum Abhaken nur meine. Die von anderen ist eine andere Art zu tracken.")
12. **Topics the owner excludes.**
    Given a topic the owner excluded in the UI (`/topics`, also for a name that does not exist yet), then `add_todo`, `update_todo` (moving a task into it) and `add_topic_note` are refused with a tool error, its open tasks are hidden from the list and the counts, and `list_topics` marks it `EXCLUDED`. Claude has no tool to change the flag. Allowing it again shows everything as before. Claude still reads the recordings; the flag stops storing, not reading.
13. **The owner can delete a topic.**
    Given a topic, then the owner can delete it on a confirmation page that says how many notes go and how many tasks stay. The topic and its notes are removed; its tasks stay without a topic, each change logged and undoable. A checkbox (on by default) keeps the topic as excluded, so the routine does not create it again. Deleting needs the CSRF token. Claude has no tool for it, and `todo_service` still has no delete function.
14. **Sorting.**
    Given the task list, then the owner can sort by priority (grouped, default), due date, newest, oldest, topic or title. Any other sort shows a flat list; an unknown sort is a 400.
15. **Topics are addressed by id and can be renamed.**
    Given a topic, then its pages live at `/topics/{id}`, so any name works, including slashes, `#`, `?` and `%`. The owner can rename it on its page; tasks, notes and the exclusion flag follow because they point at the id. An empty name, one over 120 characters, or one another topic already has (any letter case) is refused with a readable error page. Claude has no rename tool.
16. **Hardening after the security review (D-020).**
    Given a task in a topic the owner excluded, then Claude can neither find it through the duplicate check, nor change or move it (it gets the same answer as for an unknown id), nor see the topic name in `list_topics`. A digest or topic note that a later write replaces is kept as an earlier version; the digest page lists them. At most 2000 tasks may be open. The owner can let the routine read a recording again. A batch has at most 20 messages.
17. **Status "in progress" and a reminder (D-028).**
    Given a task the owner or Claude starts, then its status is `in_progress`; the "Open" tab and `list_todos` with status `open` still list it, first within its priority, and it has its own tab. Given any task in progress, then every page shows "In progress (n)" in the header; it opens a list with "Done", "Put back" and "Note" for each task. It opens only on a click. Every change of a status, in the list or in that panel, updates the count, the panel and the list without reloading (D-029). Tasks in progress are marked in amber.
18. **People to talk to (D-030).**
    Given a task, then the owner and Claude can name up to 10 people to talk to and take names off again (a logged, undoable change). The task page shows the speaker names of the task's recording (not Plaud's "Speaker N" labels) as tags: a tap adds a name, a second tap takes it off, each tap is saved at once (D-031); a name picked from the suggestions, or typed and confirmed with Enter or by leaving the field, becomes a tag at once; every known name is suggested. A click on a name in the list shows only that person's tasks; `list_todos` has the same `person` filter.
19. **The task page saves itself (D-032).**
    Given the task page, then there is no Save button: a changed field (title, detail, status, priority, due date, topic) is saved in the background when it is left, the page stays where it is and shows "Gespeichert" or, for a rejected value such as an empty title, "Nicht gespeichert" without changing the stored task. A changed status updates the status badge and the in-progress count in the header.
20. **Formatted text (D-033).**
    Given Markdown in a task's details, a recording summary or an overview, then it is shown formatted (headings, nested and numbered lists, task boxes, quotes, rules, tables, bold, italics, code) and never as raw HTML. On the task page a click on the details opens the text for editing; leaving it saves and shows it formatted again.
21. **Administration and people care (D-034).**
    Given the header link "Verwaltung", then `/admin` lists the admin tools as tiles (people, cleanup; later tools are added there). On "Personen" the owner sees every name from tasks and kept transcripts with its counts and can rename it (onto an existing name it merges), or delete it after a confirmation that shows the counts (refused with 409 if they changed). A deleted name goes from every task (logged, undoable per task); its transcript passages become "Unbekannt N". The section "Ohne Namen" lists the unnamed speakers of each kept recording with text samples, and the owner gives them a name. Discarded recordings and Plaud are never changed. Claude has no tool for any of this.
22. **Statistics (D-035).**
    Given the header link "Statistik", then `/stats` shows open tasks, tasks in progress, the net of the last four weeks and this week's conversation hours, and four charts over the last twelve weeks: new versus finished tasks, open tasks by priority at each week's end, open tasks per person (top 12) and conversation hours. Each chart has a table view. Tasks of excluded topics are left out.

## Out of scope

- Merging two topics (a rename onto an existing name is refused).
- Deleting tasks (owner decision). Deleting topics is allowed for the owner only (criterion 13).
- A public or mobile web page for the list (owner decision: Claude on the phone is enough).
- Notifications outside the web UI (the in-page reminder of criterion 17 is in scope).
- Calendar or Drive export.

## Success metric

The owner looks at `/todos` in the morning and finds the open tasks of the last days sorted by priority, without having read a recording. Source: count of tasks with `created_by = claude` that stay open versus dropped.

## Open questions

| Date | Question | Answer |
|------|----------|--------|
| 2026-10-07 | At what time does the routine run, and for how many days back on the first run? | open |
