# Requirements Process

Owner requests arrive as conversation: a wish, a complaint, an example.
Claude turns each into a written requirement **before** writing code. The
requirement is the contract; tests are written against it; the owner
confirms it in plain language.

## Where

One file per requirement: `requirements/REQ-NNN-<slug>.md`.
`NNN` is the next free number (highest + 1, never reused). `requirements/TEMPLATE.md`
is the format.

## Lifecycle

| Status | Meaning |
|--------|---------|
| `DRAFT` | Claude wrote it from the conversation; not yet confirmed |
| `APPROVED` | Owner confirmed the plain-language read-back. Code may start. |
| `IMPLEMENTED` | Merged and released; file records the version and the tests |
| `REJECTED` | Owner or mentor said no; the reason stays in the file |
| `SUPERSEDED` | Replaced by a later requirement; link it |

## Steps

1. **Capture** the owner's words verbatim in the "Owner ask" section. Do
   not paraphrase there.
2. **Draft** goal, acceptance criteria, out-of-scope and success metric.
   Every acceptance criterion has a concrete example with real-looking
   values (Given / When / Then).
3. **Read back** to the owner in their language: what the system will do,
   what it will not do, and which number will show it works. Keep it under
   ten sentences.
4. **Wait** for an explicit yes. A "sounds good" to a partial read-back
   is not approval of the whole requirement.
5. **Implement.** Every acceptance criterion maps to at least one test.
   Name tests after the criterion (`test_req_012_reminder_not_sent_twice`).
6. **Deliver**: when every criterion has a passing test, open the pull
   request immediately, without asking (`.claude/collaboration.md` §6).
7. **Close** after the merge: set `IMPLEMENTED`, fill in version, tests and release note.

## Rules

- No code before `APPROVED`, except the exceptions in
  `.claude/collaboration.md` §2.
- One requirement per feature branch and pull request. Two requirements, two PRs.
- If a question comes up during implementation that the requirement does
  not answer, ask; do not pick the convenient interpretation. Record the
  answer in the requirement under "Open questions" with the date.
- If the implementation touches a `FINAL` decision or the architecture
  rules, stop and escalate before continuing.
- Success metric: name the table and column the number comes from. A
  metric that cannot be queried is not a metric.
- Non-goals are as binding as goals. A request that hits a non-goal is a
  new requirement, not an extension.
