---
name: onboarding
description: Mandatory first-session interview for a new project built from Zenplate. Asks the owner what the project is and whether they develop themselves or only define requirements, sets the collaboration mode, and fills .claude/project.md, .claude/glossary.md and the first requirement. Use whenever .claude/project.md still carries the "ONBOARDING NOT DONE" banner or contains <placeholders>, or when the owner asks to set up, start or initialise the project.
---

# Project onboarding

This runs **before any other work** in a fresh Zenplate project. The
person you are talking to may not be a developer. Ask in their language,
one topic at a time, in plain words. Write results in English into the
files below. Do not write application code in this session.

## 1. Before asking anything

Read `.claude/project.md`. If it has no "ONBOARDING NOT DONE" banner and
no `<placeholders>`, stop: onboarding was already done. Otherwise read
`.claude/collaboration.md`, `.claude/requirements.md`, `.claude/glossary.md`
and continue. Tell the person that the first session is a short interview
and that nothing gets built yet.

## 2. Interview

Ask these in order. Wait for each answer. Reflect it back in one sentence
before moving on.

1. **What is it?** "Describe in two or three sentences what this should
   do, and for whom." → `project.md` description.
2. **Who builds it?** "Will you write code yourself, or will you describe
   what you need and let Claude build it? Do you work with a terminal or
   only with the GitHub website?"
   - Describes only, no terminal → mode `owner`.
   - Writes code or uses a terminal → mode `developer`.
   Explain what the chosen mode means in two sentences (from the table in
   `project.md`) and confirm. → `project.md` "Collaboration mode".
3. **What is it not?** "What might people expect from it that you
   explicitly do not want to build, at least for now?" Push for three
   items. → `project.md` "What this project is NOT".
4. **Who is involved?** Owner name, mentor name and how to reach them.
   → `project.md` "People".
5. **Words.** "Which words do you use for the main things in it? For
   each: what exactly does it mean, and what is it not?" Aim for five to
   ten terms; ask for units where numbers are involved. → `glossary.md`.
6. **Channel and stack.** "How do users reach it: chat bot, website,
   e-mail, something else?" Only tick optional components the owner
   named. → `project.md` "Stack".
7. **The first thing.** "If only one thing worked next week, what should
   it be?" → draft `requirements/REQ-001-<slug>.md` from
   `requirements/TEMPLATE.md`, owner's words verbatim under "Owner ask".
8. **The number.** "How will you know it works? What would you look at?"
   → success metric of REQ-001.

## 3. Write

- Replace every `<placeholder>` in `.claude/project.md` and delete the
  "ONBOARDING NOT DONE" banner.
- Fill the glossary table; code names in `snake_case` from the English meaning.
- Save REQ-001 with status `DRAFT`.
- Set `RELEASE_NOTES.md` v0.1 date to today.

## 4. Read back

Summarise in the owner's language, under ten sentences: what the project
is, what it is not, the mode, the first requirement and its acceptance
criteria. Ask for an explicit yes. On yes, set REQ-001 to `APPROVED`.

## 5. Deliver

Commit on a branch `claude/onboarding` and open a pull request titled
"Project onboarding". In `owner` mode, tell the owner to press Merge on
GitHub and that implementation of REQ-001 starts in the next session. In
`developer` mode, tell the developer the branch is ready to merge.

## 6. Do not

- Do not skip the mode question; every later rule depends on it.
- Do not skip the "what it is NOT" question; it is the cheapest scope
  guard the project will ever get.
- Do not invent terms, thresholds or features the owner did not name.
- Do not start implementing in the same session.
