# API Design Rules

Apply when the project exposes an HTTP API. Extracted from ZenTallyBot's
`api-design.md`; project-specific catalogues (endpoints, scopes) go in the
project's own section at the end.

## 1. Authorization context comes from credentials, not the URL

No tenant, customer or user-role identifiers in route paths. Resolve them
from the API key, token or signature. Prevents enumeration and keeps URLs
stable.

## 2. Versioned paths

`/api/v1/…`. Breaking changes (remove/rename field, change type, remove
endpoint, change auth) get `v2` for the affected endpoints only. Additive
changes need no new version. Old versions live at least 6 months, then
`Deprecation: true` + `Sunset: <date>` headers for 3 months.

## 3. One error schema

```json
{"error": "not_found", "message": "Item 42 does not exist.", "status": 404}
```

| Status | `error` | When |
|--------|---------|------|
| 400 | `bad_request` | Malformed JSON, missing required fields |
| 401 | `unauthorized` | Missing/invalid/expired credentials |
| 403 | `forbidden` | Valid credentials, insufficient scope |
| 404 | `not_found` | Resource does not exist within the caller's scope |
| 409 | `conflict` | Duplicate resource |
| 413 | `payload_too_large` | Body exceeds limit |
| 415 | `unsupported_media_type` | Body is not `application/json` |
| 422 | `validation_error` | Semantically wrong; field errors in `details.fields` |
| 429 | `rate_limited` | Too many requests; `Retry-After` header |
| 500 | `internal_error` | Never leaks tracebacks, SQL, paths |
| 504 | `gateway_timeout` | Upstream (e.g. LLM) exceeded timeout |

`message` is for developers, English, no stack traces. `error` codes are
stable across versions.

## 4. Request tracing

`X-Request-ID`: pass through if the client sends a valid one (≤128 chars,
alphanumeric + hyphen), else generate UUID v4. Always return it, also on
errors. Write it to every log line and every audit/usage row of the request.

## 5. Personal data: export and delete

```
DELETE /api/v1/users/{external_id}          # cascade delete, idempotent (204 twice)
GET    /api/v1/users/{external_id}/export   # all personal data as JSON
```

- Deletion order respects foreign keys; an audit row without personal data
  is written.
- Export excludes system-internal data (traces, logs, rate-limit rows).
- Scoped to the caller: no cross-tenant reads, ever.

## 6. Pagination

Cursor-based on every list endpoint: `limit` (default 50, max 100),
opaque `cursor`. Response `{"data": [...], "pagination": {"next_cursor": …, "has_more": …}}`.
Offset pagination is forbidden (skips/duplicates under concurrent writes).

## 7. Timeouts

| Category | Timeout |
|----------|---------|
| CRUD, search | 10 s |
| LLM-backed | 30 s |
| Auth | 10 s |

Server-side enforced; on timeout return 504 and persist nothing partial.

## 8. Content type & size

Request bodies are `application/json` only (else 415). Responses are
`application/json; charset=utf-8`. Body limits are explicit and documented
(e.g. 14 MB when Base64 images are accepted).

## 9. Dates and timezones

Timestamps in and out: ISO 8601 UTC (`Z`). Naive datetimes in bodies are
rejected with 422. `date` query parameters are interpreted in the
caller's configured timezone, with the 04:00 day boundary
(`.claude/code-patterns.md`).

## 10. Rate limiting

Per caller, communicated on every response via `X-RateLimit-Limit`,
`X-RateLimit-Remaining`, `X-RateLimit-Reset`. Auth endpoints get a fixed,
low limit. Expensive (LLM) endpoints get a separate, lower counter.

## 11. Documentation is generated

OpenAPI from the route definitions; Swagger UI at `/docs` outside
production only (`API_DOCS_ENABLED`). Every endpoint has summary,
description, request/response examples and documented error responses.
No hand-written API docs.

## 12. Audit log for credential events

Create / rotate / revoke / failed auth → append-only table with
`event_type`, actor, ip, `ts_utc`, JSON metadata. Never contains secrets.
Retention ≥ 1 year.

## 13. Isolation is tested

If callers are tenants or customers: integration tests prove that caller A
cannot read, list or delete caller B's rows, and that the same external id
under two callers yields two internal records.

---

## Project-specific catalogue

<Endpoints, scopes, limits for THIS project. Fill in when the API exists.>
