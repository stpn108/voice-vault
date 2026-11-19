# REQ-009: A short list per person of what was agreed

| | |
|---|---|
| **Status** | APPROVED (owner's request in chat, 2026-11-19, developer mode; the choices under "Open questions" were made by Claude and reported) |
| **Date** | 2026-11-19 |
| **Requested by** | Dennis Winter |
| **Implemented in** | — (branch `claude/hopeful-franklin-8ao6oz`) |
| **Related decisions** | D-057, D-062, D-072, D-076 |
| **Supersedes / superseded by** | Extends the person profile of D-057 |

## Owner ask (verbatim)

> Kannst Du auch eine Funktion einbauen, dass eine Bulletpoint-Liste pro Person erstellt wird, was abgesprochen / agreed wurde, auch, wenn keine konkreten Todos herausfallen? Nichts zu umfangreiches, eine max. 10-15 Bulletpoint_list, die kontinuierlich aktualisiert wid

## Goal

Before a conversation the owner sees, on the person's page, a short list of what he and this person have agreed on or settled: decisions, arrangements, who is responsible for what, also where no task came out of it. The analysis keeps the list current after every conversation with that person; it never grows beyond 15 points. The owner can correct it.

## Acceptance criteria

1. **One list per person, at most 15 points.**
   Given a person, then the profile holds a list of agreements, one short sentence each (at most 240 characters). A write with more than 15 points or a longer point is refused with a message that says so; nothing is cut off silently.
   Example: 16 points → refused, "at most 15 agreements"; the stored list is unchanged.
2. **The analysis keeps it current.**
   Given a recording with a named conversation partner, then the analysis reads the person's current list (`get_person`) and writes the whole list again (`update_person`, field `agreements`): points that still hold stay, a point a later conversation changed is replaced, new points come first, each ending with the date of the conversation. Above 15 it merges related points and drops the oldest that are settled. Mere discussion without an outcome is not an agreement.
   Example: list "Budgetfreigabe über Anna (12.11.)"; a talk on 19.11. agrees that Sven decides instead → the point is replaced by "Budgetfreigabe entscheidet Sven (19.11.)".
3. **Also without a task.**
   Given a conversation in which something was settled but nobody has to do anything, then the point goes into the list and no task is created for it.
   Example: "Wir duzen den Lieferanten nicht in Mails" → a point, no task.
4. **Shown as a list on the person's page.**
   Given a person with agreements, then the profile card shows them as a bullet list under "Absprachen"; without any, a line says that none are noted yet.
5. **The owner corrects it.**
   Given the person's page, when the owner edits the list (one point per line) and leaves the field, then it is saved like the other profile fields, bullets he typed are taken off, and the shown list follows.
   Example: "- Jour fixe alle 14 Tage" → stored and shown as "Jour fixe alle 14 Tage".
6. **Nothing is lost, the profile rules hold.**
   Given a replaced list, then the old text is kept in `text_versions` like every profile text. Excluded people get no list (the write is refused). The content rule of D-057 applies: work only. Names and terms get the glossary's spellings (D-062). When two names are merged in "Verwaltung → Personen", the target's own list wins and an empty one takes the other.

## Out of scope

- Reading recordings again that were analysed before this change: the lists fill from the next conversations on.
- A link from each point to its recording, or a history view of the list on the page.
- Agreements per topic or per task; the list is per person.
- A separate page or export of all agreements.

## Success metric

Share of people with a conversation since the release whose profile has agreements: `person_profiles.agreements <> ''` over the people in `person_profiles` with `updated_at` after the release. No extra API calls: the list travels in the `update_person` call the analysis already makes (`llm_traces`, call type `analysis`, requests per run unchanged).

## Open questions

| Date | Question | Answer |
|------|----------|--------|
| 2026-11-19 | Where is the list shown? | In the profile card on the person's page (Claude's choice). |
| 2026-11-19 | How many points? | 15, the upper end of the owner's "10-15" (Claude's choice). |
| 2026-11-19 | Older recordings? | Not read again; the list starts empty (Claude's choice, to avoid a second paid run over everything). |

## Tests

<Filled in at IMPLEMENTED: test file and test names per criterion.>
