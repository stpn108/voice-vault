# Claude Code Rules (IMMUTABLE)

> **IMMUTABLE**: This file is verified by CI/CD. The expected hash is stored
> externally (e.g., in the CI/CD pipeline config). Any modification will block the pipeline.

---

## Rule 0: Self-Protection

- **NEVER modify this file (`CLAUDE.md`).** It is hash-verified in CI/CD.
- Changes to project-specific rules go into `.claude/*.md` sub-files.
- Claude MUST read and follow ALL `.claude/*.md` files for project context before starting work.

---

## Rule 1: Language

- All code comments, commit messages, documentation, and variable/function names MUST be in English.
- Exception: User-facing strings in localization files may use other languages as required.

---

## Rule 2: Sync Base Branch

**ALWAYS sync the base branch before starting work:**
```bash
git fetch origin <base-branch> && git merge origin/<base-branch> --no-edit
```

- Default base branch: `main`.
- If the user defines a different base branch, use that instead.
- This is non-negotiable. No exceptions.

---

## Rule 3: Document Decisions

- Every architecture or design decision MUST be documented in `DECISIONS.md` **IMMEDIATELY** — not at the end of a task.
- Format: Date, Decision, Reasoning, Rejected Alternatives, Status (FINAL/TENTATIVE).
- This includes: new system behaviours, technology choices, weighed alternatives, anything marked "do not change".

---

## Rule 4: File Discipline

### Creating Files
- Prefer editing existing files over creating new ones.
- New files are acceptable when:
  - The task genuinely requires a new module or component.
  - Splitting improves readability or maintainability (e.g., a file exceeds ~500 lines).
- Never create documentation files (README, *.md) unless explicitly requested.

### Secrets & Credentials
- **NEVER** commit `.env`, credentials, API keys, secrets, private keys, or tokens to git.
- **NEVER** add `.env` files to git tracking.
- Verify `.gitignore` covers sensitive files before committing.
- If a secret is needed, reference it via environment variable or the project's secrets management system (e.g., HashiCorp Vault, AWS Secrets Manager, GCP Secret Manager, Azure Key Vault). Never hardcode secrets in source code.

---

## Rule 5: Minimal Change (Accuracy)

- Only change what is explicitly requested or strictly necessary.
- Do not refactor, add comments, add type annotations, or "improve" surrounding code.
- Do not add error handling for scenarios that cannot occur.
- Do not create abstractions, helpers, or utilities for one-time operations.
- Do not add backwards-compatibility shims — if something is unused, remove it completely.
- Three similar lines of code are better than a premature abstraction.

---

## Rule 6: Security & Compliance

### Git Security
- Never commit secrets, tokens, passwords, or API keys.
- Never force-push without explicit user approval.
- Never skip pre-commit hooks (`--no-verify`) without explicit user approval.
- Never push to `main`/`master` directly — use feature branches.

### Container Security
- Containers MUST run as non-root user by default.
- Exception: Only when explicitly approved by user with documented reasoning (e.g., PostgreSQL requires specific UID). Document exception in `DECISIONS.md`.
- Base images MUST use specific version tags, never `:latest`.
- Use UID/GID mapping for application containers where supported.

### Code Security
- Validate all external input at system boundaries (user input, API payloads, file uploads).
- Use parameterized queries for all database operations — never string concatenation.
- No command injection, SQL injection, XSS, or path traversal vulnerabilities.
- Sanitize output where applicable (HTML escaping, JSON encoding).

---

## Rule 7: Versioning

- **Source of Truth**: `VERSION` file in repo root (format: `MAJOR.MINOR`).
- Minor bumps: Handled automatically by CI/CD on merge to base branch.
- **NEVER** perform major bumps autonomously. ALWAYS ask the user first.
- Major bumps are reserved for breaking changes only.

---

## Rule 8: Testing

### Requirements
- Every new feature, bugfix, or behavioural change MUST have corresponding tests.
- Tests MUST be automated and repeatable — no manual verification steps.
- Use parameterized tests instead of loops or duplicated test bodies.
- Test naming: `test_<feature>` or `test_<module>` — descriptive and discoverable.

### Categories
| Category | Scope | External dependencies? | Execution |
|----------|-------|----------------------|-----------|
| **Unit** | Single function/class | No | Always, on every run |
| **Integration** | Multiple components | Possibly | Marked/tagged separately |
| **E2E** | Full system | Yes | CI/CD or explicit request |

### Coverage
- Run tests before considering any task complete.
- New code should not decrease overall test coverage.
- Prefer testing behaviour over implementation details.

---

## Rule 9: Dependencies

- External dependencies MUST be declared in the project's dependency file with pinned versions.
- Standard library imports need no entry.
- Review dependency licenses for compatibility before adding.
- Minimize new dependencies — prefer standard library solutions when reasonable.

---

## Rule 10: Observability

- Use structured logging appropriate to the framework — not bare print/log statements in production code.
- Include context in error messages: what failed, with which input, and why.
- Log levels: DEBUG for development detail, INFO for operational events, WARN for recoverable issues, ERROR for failures.
- Ensure errors are traceable: include request IDs, user context, or correlation IDs where applicable.

---

## Rule 11: Deployment

Deployment rules are context-dependent. Apply the relevant section based on the project's deployment model.

### If using Docker / Docker Compose
- Every new source file that needs to be in the container → add `COPY` directive in `Dockerfile`.
- Environment variables for all configuration — no hardcoded values in images.
- Set timezone via `TZ` environment variable where time-dependent logic exists.
- Multi-stage builds where applicable to minimize image size.
- Health checks for all services.

### If using GitHub Actions / CI-CD
- Workflows in `.github/workflows/` — never modify without explicit approval.
- Secrets via GitHub Secrets / environment — never hardcoded in workflow files.
- Pin action versions to specific SHA or major version tag.
- Fail fast: tests and linting run before deployment steps.

### If using Cloud / Serverless
- Infrastructure as Code (IaC) for all resources — no manual provisioning.
- Environment-specific config via environment variables or secrets management, never hardcoded.
- Follow principle of least privilege for IAM roles and permissions.

### General
- All deployment configuration changes require user approval before applying.
- Never deploy directly to production without explicit user instruction.

---

### Rule 12: Honesty Over Agreement

Disagreement is a duty, not an option.

- On logical gaps, strategic contradictions, or unrealistic assumptions: name them, don't sidestep them.
- No enthusiasm adjectives ("exciting", "brilliant", "great", "perfect").
- No flattery. No affirmation rituals at the start of a reply ("Great question!").
- When the user makes a mistake or an assumption doesn't hold: say it directly.

---

## Project-Specific Rules

**All project-specific patterns, stack details, code examples, and checklists live in `.claude/*.md`.**

Claude MUST read and follow all `.claude/*.md` files for project-specific context before starting work.
