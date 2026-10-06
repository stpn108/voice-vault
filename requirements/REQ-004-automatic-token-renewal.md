# REQ-004: Automatic Plaud token renewal

| | |
|---|---|
| **Status** | APPROVED (owner's explicit ask in chat, developer mode) |
| **Date** | 2026-10-06 |
| **Requested by** | Dennis Winter |
| **Implemented in** | — (branch `claude/festive-bohr-31plpp`, not merged) |
| **Related decisions** | D-005, D-001 |
| **Supersedes / superseded by** | — |

## Owner ask (verbatim)

> Ich habe die beiden Tokens jetzt. Ich habe einen Bearer-Token und ich habe einen Refresh-Token. Wie muss ich die jetzt hinterlegen? Weil der Bearer-Token ist ja jetzt bald dann outdated wahrscheinlich.

> Nein, ich will mich nicht manuell einloggen. Was soll das denn? Ich will, dass im Hintergrund ein Prozess läuft.

## Goal

The owner pastes the access token and the refresh token once. From then on the background process keeps itself signed in to Plaud without any manual step, until Plaud invalidates the refresh token.

## Acceptance criteria

1. **Seed once.**
   Given `PLAUD_REFRESH_TOKEN` (and optionally `PLAUD_TOKEN`) in `.env` and an empty database, when the app starts, then the seed is stored in `plaud_sessions`. With the refresh token alone, the first cycle fetches the access token.
2. **Renew ahead of expiry.**
   Given an access token with 5 hours left of a 24 hour lifetime, when a cycle starts, then it is renewed before any other Plaud call.
3. **Rotation is kept.**
   Given Plaud answers a renewal with a new refresh token, then the new pair is stored, and after a restart with the unchanged `.env` the stored pair is used.
4. **New seed wins.**
   Given the owner pastes different tokens into `.env`, when the app starts, then they replace the stored pair.
5. **Recover from 401.**
   Given Plaud answers a call with HTTP 401, when a refresh token exists, then the token is renewed once and the call is sent again once; a second 401 raises an authentication error.
6. **Dead refresh token stops the cycle.**
   Given Plaud rejects the refresh token, then the cycle stops before any deletion and the owner is warned (log, mail if SMTP is set, at most once a day).
7. **Transient errors do not stop a valid token.**
   Given a renewal fails with a network error or HTTP 5xx and the access token is still valid, then the cycle continues with the current token.
8. **Region and host safety.**
   Given a renewal answers with a region redirect, then it is followed once and only to a `*.plaud.ai` host; the refresh cookie is never sent to any other host.

## Out of scope

- E-mail and password login.
- Plaud's official OAuth API.
- Encrypting tokens at rest in the database.

## Success metric

Days between manual token pastes. Target: none after the first one, until Plaud invalidates the refresh token. Source: log lines "Plaud token refreshed" and table `plaud_sessions.refreshed_at`.

## Open questions

| Date | Question | Answer |
|------|----------|--------|
| 2026-10-06 | Endpoint and cookie names come from Plaud-Sync's source, not from a live test. Does the renewal work from the server? | open, `plaud_live_check.py` |
| 2026-10-06 | Does Plaud rotate the refresh token on every renewal, and does it log out the browser session? | open, live check log shows `rotated=` |
| 2026-10-06 | Is the refresh token a JWT with a readable `exp`? Otherwise the expiry warning cannot fire. | open |

## Tests

| Criterion | Tests |
|---|---|
| 1 | `test_plaud_auth.py::test_first_load_seeds_store_from_environment`, `test_plaud_client.py::test_req_004_refresh_token_alone_is_enough_the_access_token_is_fetched_on_the_first_cycle` |
| 2 | `test_plaud_auth.py::test_needs_refresh_by_remaining_lifetime`, `test_plaud_client.py::test_ensure_fresh_token_refreshes_when_due`, `test_sync_job.py::test_req_004_cycle_renews_token_before_importing` |
| 3 | `test_plaud_auth.py::test_refresh_sends_refresh_cookie_and_stores_rotated_pair`, `test_stored_pair_wins_when_seed_is_unchanged` |
| 4 | `test_plaud_auth.py::test_changed_seed_replaces_stored_pair` |
| 5 | `test_plaud_client.py::test_req_004_401_triggers_one_refresh_and_the_call_is_retried_with_the_new_token`, `test_second_401_after_refresh_raises_instead_of_looping` |
| 6 | `test_sync_job.py::test_req_004_rejected_refresh_token_aborts_cycle_and_mails` |
| 7 | `test_plaud_client.py::test_ensure_fresh_token_keeps_using_valid_token_when_refresh_has_a_transient_error` |
| 8 | `test_plaud_auth.py::test_refresh_redirect_to_foreign_host_is_refused_and_cookie_never_sent_there` |
