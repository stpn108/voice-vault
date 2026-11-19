# REQ-008: Task board with status columns, a one-line table, and tasks as the home page

| | |
|---|---|
| **Status** | APPROVED (owner's decisions in chat, 2026-11-19, developer mode) |
| **Date** | 2026-11-19 |
| **Requested by** | Dennis Winter |
| **Implemented in** | — (branch `claude/hopeful-franklin-8ao6oz`) |
| **Related decisions** | D-028, D-037, D-047, D-048, D-074 |
| **Supersedes / superseded by** | Amends REQ-002 (the recordings list is no longer the home page) |

## Owner ask (verbatim)

> Kannst du dir bitte mal die Übersichtsseite angucken der Aufgaben? Im Moment ist es für mich irgendwie vom Handling her noch ein bisschen hackelig. Ich habe eine Liste mit dringenden Aufgaben, die stehen ganz oben. Dann habe ich eine Liste mit hoch. Das ist dann aber auch eine Liste. Mir fällt es ein bisschen schwer, das zu korrelieren. […] Die begonnen sind, die haben eine Farbmarkierung und sind ganz oben. Die nicht begonnen sind, die sind einfach darunter. Irgendwie fehlt mir so ein bisschen irgendwie die Übersicht, weil es sind sehr viele. Ich habe jetzt zwischenzeitlich 26 in Arbeit und gefühlt eben irgendwie sind mindestens nochmal genauso viele offen und ich scroll mich irgendwie tot […]. Und dazu kommt, ich schaue mir die Aufnahmen an sich eigentlich selten an. Das heißt also, […] die Aufnahmen sollten nicht die erste Seite sein, auf die ich komme, wenn ich auf Home gehe. […] Und vielleicht so Richtung Jira wäre ein bisschen besser. Also, dass man wirklich eben irgendwie Lanes hat, beziehungsweise Zustände, die man halt eben so ein bisschen tabellarischer darstellt.

Decision after a clickable draft with invented tasks: "Board mit Tabelle, Details am Rand später, Board als Startseite".

## Goal

The owner sees his unfinished tasks on one or two screens instead of seven: status as columns (open, in progress, done lately), priority as rows, one compact card per task. He moves a task by dragging it or with the buttons on the card. A second view shows the same unfinished tasks as a table with one line per task. Opening the app leads to the tasks; the recordings are one click away in the navigation.

## Acceptance criteria

1. **Tasks are the home page.**
   Given the owner opens `/`, then he lands on the task board. The recordings list is at `/recordings`, with search, paging, "Jetzt abholen", discard and cleanup leading back there. The navigation reads Aufgaben, Themen, Tagesübersicht, Statistik, Aufnahmen.
   Example: `GET /` → redirect to `/todos`; `GET /recordings?q=budget` → the search result.
2. **Columns are the status.**
   Given the owner's own tasks, then the board has the columns "Offen", "In Arbeit" and "Erledigt". "Erledigt" holds only tasks finished within the last 7 days, the latest first, and links to the full list. Dropped tasks and "Zusagen an mich" are not on the board. Each column head shows its number of cards.
   Example: 30 open, 26 in progress, 6 finished this week, 40 finished earlier → 30 / 26 / 6.
3. **Rows are the priority.**
   Given the board, then it has one row each for Dringend, Hoch, Normal and Niedrig, in this order, each with its number of open and started tasks. A row can be folded; "Niedrig" starts folded. Within a cell, tasks with a due date come first, the earliest first.
   Example: urgent task A (open) and urgent task B (in progress) sit in the same row, in neighbouring cells.
4. **Rows by topic, or none.**
   Given `group=topic`, then the rows are the topics of the shown tasks in alphabetical order, tasks without a topic last; with `group=none` there is one row.
   Example: tasks in "Budget 2027" and "Messe Frankfurt" and one without topic → three rows.
5. **A card is compact and complete.**
   Given a task, then its card shows the title (link to the task page), a dot in the colour of the priority, the topic (unless rows are topics), the due date (red and "Überfällig" when past) or else the age, the number of linked tasks, the people as initials (excluded people left out, D-055), the mark for unclear names (D-062), and "neu" for a task created within the last 2 days.
   Example: "Vertragsentwurf an Anwalt weiterleiten", urgent, due 3 days ago, 2 links, Miriam Vogt → red dot, "Überfällig 16.11.", "🔗 2", "MV".
6. **Moving with buttons, without a script.**
   Given a card, then it has the check button (done; on a finished card: reopen) and "Beginnen" or "Zurückstellen" and "Nicht relevant", posting to the existing status route; the change is logged and undoable as before.
   Example: "Beginnen" on an open task → status `in_progress`, event `started`.
7. **Moving by dragging.**
   Given the board with the page script, when the owner drags a card into another cell, then the task gets that column's status and, with rows by priority, that row's priority, in one logged change. The counts in the column heads, the rows and the "In Arbeit" reminder in the header follow without a reload.
   Example: open/normal task dragged into "In Arbeit" of "Dringend" → `in_progress`, priority 1, one event.
8. **Quick filters.**
   Given the board, then three filters show their number of unfinished tasks and narrow the board to them: overdue, due within 7 days (today included), new (created within 2 days). A topic or person filter (`?topic=`, `?person=`) narrows the board like the list.
   Example: 5 overdue tasks → "Überfällig 5"; a click shows these 5.
9. **Links are visible.**
   Given a task with linked tasks, then pointing at its card highlights the linked cards that are on the board and draws lines to them.
10. **Table view.**
    Given `view=table`, then the unfinished tasks show as one line each in columns: check, title, status, topic, people, due, age, priority, links. Everything the list could do stays: check off, start, put back, drop, sort, draw a link. On a narrow screen the table falls back to the list layout.
    Example: 58 unfinished tasks → 58 lines.
11. **The other lists stay.**
    Given "In Arbeit", "Erledigt", "Verworfen" or "Zusagen an mich", then the list is as before.

## Out of scope

- A side panel with the task details on the board (owner: later). A click on a card opens the task page.
- Creating links on the board; that stays in the table, on the task page and in the Mindmap.
- A board for "Zusagen an mich".
- A limit or warning for the number of tasks in progress.
- Searching tasks by text.
- Remembering the chosen view or filter between visits (folded rows are remembered in the browser).
- Dragging on touch screens; there the buttons on the card move a task.

## Success metric

Not a database number: the height of the page. With 58 unfinished tasks at 1440×900 the board is under two screens high with "Niedrig" folded (the list: seven). The use of the board shows in `todo_events`: rows with `kind` in (`started`, `reopened`, `completed`) and `actor = 'owner'` per day.

## Open questions

| Date | Question | Answer |
|------|----------|--------|
| 2026-11-19 | Board with table, or table only? | Board with table. |
| 2026-11-19 | Details at the side of the board? | Later. |
| 2026-11-19 | Board as the home page? | Yes. |

## Tests

<Filled in at IMPLEMENTED: test file and test names per criterion.>
