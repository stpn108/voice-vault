# REQ-014: The report shows what was finished lately and has a copy button

| | |
|---|---|
| **Status** | APPROVED (owner's request in chat, 2026-11-27, developer mode; the choices under "Open questions" were made by Claude and reported) |
| **Date** | 2026-11-27 |
| **Requested by** | Dennis Winter |
| **Implemented in** | — (branch `claude/req-014-report-copy-and-done`) |
| **Related decisions** | D-027, D-083, D-084, D-085 |
| **Supersedes / superseded by** | Changes REQ-013: criterion 2 (finished tasks are not listed) and its out-of-scope items "finished tasks" and "a copy button". |

## Owner ask (verbatim)

> Kopieren-Knopf noch dazu, bitte. Und erledigte Aufgaben

Asked right after REQ-013 was delivered, whose report had left out both. After the first delivery:

> Und bei dem Report, da will ich nicht auf Anzeigen klicken müssen, sonst haben wir das auch überall, dass quasi on Change
> schon die Dinge abgeschickt werden. Also auch bei der Auswahl von Leuten bitte on Change das Dropdown so konfigurieren, dass
> der Anzeigen-Button daneben weg kann. Und der Kopieren-Button, den hätte ich mir jetzt eigentlich erwartet, dass es ein
> kleines Icon wird innerhalb von diesem Div, in dem, also rechts oben, dann in dem auch der Content angezeigt wird.

## Goal

The report covers a whole status update: what was finished lately, what is in progress, what is open. One click
puts it into the clipboard as plain text, ready to paste into a chat or a mail.

## Acceptance criteria

1. **Finished tasks come first.**
   Given the owner finished "Rechnung geprüft" an hour ago, "Altes Thema" two days ago and "Lange her" eight days
   ago, dropped "Verworfen", and finished "Urlaub gebucht" in an excluded topic, when he opens `/report`, then the
   first group is "Erledigt, letzte 7 Tage" with "Rechnung geprüft", then "Altes Thema"; the others are not listed.
   The window is the one the board keeps finished tasks for (`task_board.DONE_DAYS`, 7 days).
   Given Anna fulfilled the promise "Schon geliefert" yesterday, when the owner picks Anna, then it is listed under
   "Erledigt, letzte 7 Tage".
2. **The groups read finished, in progress, open.**
   Given the page, then the groups are "Erledigt, letzte 7 Tage", "In Arbeit", "Offen", in this order; an empty group
   is left out.
3. **A copy icon in the card.**
   Given a report with tasks, then a small copy icon at the top right inside the card (label "Kopieren" for screen
   readers and as tooltip) puts the report into the clipboard as plain text: each group's heading on a line, each
   title as "- <title>", a blank line between groups, and "… und weitere" at the end when the list was cut. After the
   click the icon turns into a check mark and "Kopiert" shows next to it for two seconds. Where the browser gives no
   clipboard access, the lists on the page are selected and "Markiert, jetzt Strg+C" shows next to the icon.
   Example: "Erledigt, letzte 7 Tage\n- Rechnung geprüft\n\nIn Arbeit\n- Angebot schreiben\n\nOffen\n- Budget prüfen".
   Given an empty report, then there is no icon. Without JavaScript the icon does not show.
4. **The limit holds for finished tasks too.**
   Given more than 200 finished tasks in the window, then the first 200 are listed and the line saying there are
   more shows.
5. **The dropdown shows the pick at once.**
   Given the report of "Ich", when the owner picks Anna in the dropdown, then the page shows Anna's report without a
   further click; there is no "Anzeigen" button. Without JavaScript a button "Anzeigen" is the fallback.

## Out of scope

- Choosing the time window on the page.
- Dropped tasks.
- Any other format than plain text (Markdown, rich text, a mail).

## Success metric

No number of its own; the page is read only. Finished tasks come from `todos` (status `done`, `done_at` within the
last 7 days, same `kind` and person rules as REQ-013).

## Open questions

| Date | Question | Answer |
|------|----------|--------|
| 2026-11-27 | Which finished tasks? | Those of the last 7 days, the window the board uses (Claude's choice). |
| 2026-11-27 | Where does the group go? | First: a status update starts with what is done (Claude's choice). |
| 2026-11-27 | What does the copied text look like? | Headings and "- " bullets, plain text (Claude's choice). |

## Tests

<Filled in at IMPLEMENTED: test file and test names per criterion.>