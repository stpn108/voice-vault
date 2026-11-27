# REQ-013: A report page with the titles of what someone is working on, for a status update

| | |
|---|---|
| **Status** | IMPLEMENTED (owner's request in chat, 2026-11-27, developer mode; the reading of "assigned to a person" and the other choices under "Open questions" were made by Claude and reported) |
| **Date** | 2026-11-27 |
| **Requested by** | Dennis Winter |
| **Implemented in** | v0.61 (branch `claude/req-013-status-report`) |
| **Related decisions** | D-015, D-028, D-038, D-044, D-077, D-083 |
| **Supersedes / superseded by** | Changed by REQ-014: finished tasks are listed (criterion 2) and there is a copy button (both were out of scope here). |

## Owner ask (verbatim)

> Kannst du mir noch irgendwo eine Stelle schreiben, in der ich meinen eigenen Report erstellen kann? Also wo ich,
> was weiß ich, eine Dropdown-Liste habe. Und da kann ich einfach eben irgendwie mich auswählen. Per Default steht
> da ich. Und ansonsten kann ich anderen Leute auswählen und ich bekomme dann einfach nur eine Bullet-Point-Liste,
> ganz High-Level, an was die Leute gerade arbeiten. Das fände ich ganz cool. Also einfach nur die Titel quasi von
> den Tasks, die den jeweiligen Leuten zugeordnet sind. Dass ich einen Status-Update zum Beispiel geben kann, wenn
> ich in einem Meeting bin, an was ich arbeite.

## Goal

In a meeting the owner opens one page and reads off what he is working on: a short bullet list of task titles,
nothing else. A dropdown switches to another person and shows what that person is working on for him.

## Acceptance criteria

1. **A page "Report" in the navigation.**
   Given the app, then the navigation has "Report" after "Personen", leading to `/report`.
2. **"Ich" is the default and shows the owner's own tasks.**
   Given the owner's tasks "Angebot schreiben" (in progress, priority 2), "Budget prüfen" (open, priority 1),
   "Folien bauen" (open, priority 3), "Altes Thema" (done) and "Urlaub planen" (open, in an excluded topic), when
   the owner opens `/report`, then the dropdown shows "Ich", and the page lists under "In Arbeit" "Angebot schreiben"
   and under "Offen" "Budget prüfen", then "Folien bauen"; finished, dropped and excluded tasks are not listed.
3. **Another person shows what they promised the owner.**
   Given Anna promised "Zahlen liefern" (open) and "Vertrag prüfen" (in progress), and the owner has his own task
   "Anna anrufen" naming her, when the owner picks Anna in the dropdown, then the page lists under "In Arbeit"
   "Vertrag prüfen" and under "Offen" "Zahlen liefern"; "Anna anrufen" is not listed.
4. **The dropdown holds the known people.**
   Given the names on tasks and the named speakers of kept recordings, then the dropdown lists "Ich" first and then
   every such person in alphabetical order; excluded people (D-044) and unnamed speakers are missing.
   A name that is not a known person gives a 404.
5. **Only the titles, as a bullet list.**
   Given the page, then each task is one bullet with its title as plain text, without links, status, priority,
   dates or people, so the list can be copied as it is. Within a group the order is the one of the task list:
   priority, then due date.
6. **Nothing open says so.**
   Given a person with no unfinished task, then the page says "Gerade nichts offen."
7. **Long lists are cut and say so.**
   Given more than 200 unfinished tasks, then the first 200 are listed and a last line says that there are more.

## Out of scope

- Finished tasks ("what I did last week").
- A copy button, a PDF or a mail; the list is selected and copied by hand.
- Topics, the profile's "current work" or agreements on the page.
- Editing on the page.

## Success metric

No number of its own; the page is read only. It reads `todos` (status `open` or `in_progress`, `kind` `mine` for
"Ich", `kind` `waiting` with the name in `people` for anyone else, not in an excluded topic), the same rows the
task list shows.

## Open questions

| Date | Question | Answer |
|------|----------|--------|
| 2026-11-27 | Which tasks are "assigned" to another person? | What they promised the owner ("Zusagen an mich"). The owner's own tasks that name a person are his work, not theirs (Claude's choice). |
| 2026-11-27 | One list or grouped? | Two groups, "In Arbeit" first, then "Offen" (Claude's choice). |
| 2026-11-27 | Where? | Its own page "Report" in the navigation (Claude's choice). |

## Tests

All in `app/tests/test_report.py`.

| Criterion | Tests |
|-----------|-------|
| 1 Navigation | `test_req_013_report_is_in_the_navigation_after_people` |
| 2 "Ich" by default | `test_req_013_by_default_the_report_lists_the_owners_unfinished_tasks_by_priority`, `test_req_013_the_page_shows_me_by_default_with_my_tasks_as_bullets` |
| 3 Another person | `test_req_013_for_another_person_the_report_lists_what_they_promised`, `test_req_013_picking_a_person_shows_what_they_promised` |
| 4 Dropdown | `test_req_013_the_dropdown_lists_me_then_the_known_people_without_excluded_and_unnamed`, `test_req_013_a_name_that_is_not_a_known_person_is_a_404` |
| 5 Titles only | `test_req_013_titles_are_plain_escaped_text_without_links` |
| 6 Nothing open | `test_req_013_nothing_open_says_so` |
| 7 Long lists | `test_req_013_a_long_report_is_cut_and_says_so`, `test_req_013_a_long_page_says_that_there_are_more` |