# REQ-010: A dashboard of all people, with the state of the tasks per person and a search

| | |
|---|---|
| **Status** | APPROVED (owner's request in chat, 2026-11-19, developer mode; the reading of the request and the choices under "Open questions" were made by Claude and reported) |
| **Date** | 2026-11-19 |
| **Requested by** | Dennis Winter |
| **Implemented in** | — (branch `claude/hopeful-franklin-8ao6oz`) |
| **Related decisions** | D-038, D-044, D-057, D-060, D-076, D-077 |
| **Supersedes / superseded by** | — |

## Owner ask (verbatim)

> Und so ein Person-Based Dashboard bitte auch!

> Dann kann ich auch sehen, in welchem Zustand die Tickets sind

> Und gib mir eine einfache Suche nach einer Person

Asked right after the list of agreements per person (REQ-009) and the task board (REQ-008); the second and third message came while the dashboard was being built.

## Goal

One page shows all people the owner deals with, one card each, so he sees at a glance with whom something is open: when they last talked, how many of his tasks with the person are open and how many in progress, what the person owes him and whether something is overdue, what to raise next time and the latest agreements. A click leads to the person's page. The task board can show the same per person: one row per person, the status as columns. A search finds a person by name.

## Acceptance criteria

1. **A page "Personen" in the navigation.**
   Given the app, then the navigation has "Personen" after "Aufgaben", leading to `/people`.
2. **One card per known person.**
   Given the names on tasks and the named speakers of kept recordings, then each has one card; excluded people (D-044) and unnamed speakers have none.
   Example: tasks name Anna and Jochen, a recording has the speakers Jochen and "Speaker 2", Eve is excluded → cards for Anna and Jochen.
3. **What a card shows.**
   Given a person, then the card shows the name (link to the person's page), the role from the profile, when the owner last talked to them and in how many conversations, the number of the owner's tasks with them that are open and that are in progress (both lead to the board of that person's tasks), the number of open promises they owe him, the number of these tasks and promises that are overdue, what to raise next time, and the first three agreements with the number of further ones. The numbers are the ones the person's page shows.
   Example: Anna, 3 open tasks, 1 in progress, 2 promises of which 1 was due yesterday, 5 agreements → "3 Offen", "1 In Arbeit", "2 Schuldet Dir", "1 überfällig", three agreements and "+2 weitere".
4. **Sorted by what matters.**
   Given the page, then the cards are sorted by the last conversation, the latest first, people without a conversation last. The owner can switch to the number of open points (tasks plus promises, most first) or the name.
5. **Leads back.**
   Given a person's page opened from the dashboard, then its breadcrumb leads back to "Personen".
6. **The state of the tasks per person on the board.**
   Given the task board with `group=person`, then there is one row per person named on the shown tasks, in alphabetical order, and a last row "Ohne Person"; the columns stay the status. A task with two people has a card in both rows and counts once in the column heads; moving one card moves the other. Excluded people get no row. The row's name leads to the person's page.
   Example: "Angebot schreiben" (in progress) with Anna and Jochen → a card under "In Arbeit" in both rows, column head "In Arbeit 1".
7. **A simple search for a person.**
   Given the dashboard, when the owner types part of a name (any case, with suggestions of the known names) and searches, then only the people whose name contains it are shown; if exactly one person matches, that person's page opens; if none, the page says so.
   Example: "becker" with one Anna Becker → her page; "an" with Anna and Joanna → two cards.

## Out of scope

- Editing on the dashboard; profiles and agreements are corrected on the person's page.
- A search over anything but the name (role, agreements, tasks).
- Knowing which of the people is the owner himself: if his own name shows up as a card, he excludes it under "Verwaltung → Personen".
- A board of "Zusagen an mich" per person; the cards count them and link to that list.

## Success metric

No number of its own; the page is read only. Its counts come from `todos` (unfinished, `people` contains the name, `kind` mine or waiting, `due_date` before today) and from `segments`/`recordings` (conversations, last one), the same rows the person's page reads.

## Open questions

| Date | Question | Answer |
|------|----------|--------|
| 2026-11-19 | What is a "Person-Based Dashboard"? | Built both readings after the owner's second message: an overview page with one card per person, and rows by person on the task board. |
| 2026-11-19 | Where is the search? | On the dashboard page; one hit opens the person directly (Claude's choice). |
| 2026-11-19 | Default order? | Last conversation first (Claude's choice). |

## Tests

<Filled in at IMPLEMENTED: test file and test names per criterion.>
