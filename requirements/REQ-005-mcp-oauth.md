# REQ-005: OAuth sign-in for the MCP server (claude.ai)

| | |
|---|---|
| **Status** | APPROVED (owner's explicit ask in chat, developer mode) |
| **Date** | 2026-10-07 |
| **Requested by** | Dennis Winter |
| **Implemented in** | — (branch `claude/festive-bohr-31plpp`, not merged) |
| **Related decisions** | D-011, D-012 |
| **Supersedes / superseded by** | extends REQ-003 |

## Owner ask (verbatim)

> MCP_ALLOWED_HOSTS mit oder ohne https://? Und es braucht Oauth.

## Goal

The owner connects claude.ai (web, desktop, mobile) to the MCP server of REQ-003 as a custom connector. Claude signs in through OAuth: it sends the owner to an approval page of his own server, he enters a password, and from then on Claude holds short-lived tokens that renew themselves. The static token of REQ-003 keeps working for Claude Code.

## Acceptance criteria

1. **Discovery.**
   Given OAuth is configured, when Claude calls `/mcp` without a token, then it gets 401 with `WWW-Authenticate: Bearer … resource_metadata="<MCP_PUBLIC_URL>/.well-known/oauth-protected-resource"`. The protected-resource document names the resource `<MCP_PUBLIC_URL>/mcp` and this server as the only authorization server. The authorization-server document advertises authorization and token endpoint, response type `code`, grant types `authorization_code` and `refresh_token`, PKCE method `S256` only, scopes `mcp` and `offline_access`, no registration endpoint.
2. **Only one known client.**
   Given a request with another client ID, or a redirect address that is not on the list (default: `https://claude.ai/api/mcp/auth_callback`), then the server shows an error page and never redirects. A listed loopback address without a port matches any port.
3. **PKCE is mandatory.**
   Given no `code_challenge`, a method other than `S256`, or a malformed challenge, then the request ends with a redirect carrying `error=invalid_request`. A wrong `code_verifier` at the token endpoint is `invalid_grant`.
4. **Owner approval with a password.**
   Given the approval page, when the owner enters `MCP_OAUTH_PASSWORD`, then the browser is redirected to Claude's callback with a one-time `code`, the original `state` and `iss`. Deny returns `access_denied`. The form carries a signed, 10-minute request; a changed redirect address, a wrong signature or an expired form is rejected.
5. **Wrong passwords get slower.**
   Given wrong passwords, then no code is issued, every failure is logged without the password, and each next attempt waits twice as long (1 s, 2 s, 4 s, up to 30 s). A correct login resets the wait.
6. **Tokens.**
   Given a valid code and `code_verifier`, then the token endpoint (form-encoded) returns an access token (1 hour) and a refresh token (30 days). Only SHA-256 hashes are stored. A code works once; using it again fails and revokes the tokens it produced.
7. **Refresh rotation.**
   Given a refresh token, then it returns a new pair and the old refresh token is spent. Presenting a spent refresh token fails with `invalid_grant` and revokes the whole family. Expired or revoked tokens are refused.
8. **MCP access.**
   Given a valid access token, then `/mcp` accepts it like the static token. A refresh token, an expired, a revoked or an unknown token is answered with 401.
9. **Client secret optional.**
   Given `MCP_OAUTH_CLIENT_SECRET`, then the token endpoint requires it (form field or HTTP Basic); without it the client is public.
10. **Safe or no start.**
    Given any OAuth setting without a complete, safe configuration (password under 20 characters, client ID under 8, public URL missing, with a path or without https, empty or unsafe redirect addresses), then the service refuses to start. OAuth alone, without `MCP_TOKENS`, is enough to start.
11. **Host names may be pasted as URLs.**
    Given `MCP_ALLOWED_HOSTS=https://mcp.example.org/mcp`, then the host `mcp.example.org` is allowed.

## Out of scope

- Dynamic client registration and client ID metadata documents: no open endpoint that anyone on the internet can call.
- Several users or accounts: one owner, one password.
- A Claude Code sign-in through OAuth: Claude Code uses the static token.
- An admin page: revoking is one SQL statement (README).

## Success metric

claude.ai connects with the client ID and password only, and stays connected for 30 days without another approval. Source: log lines `OAuth tokens issued grant=refresh_token`.

## Open questions

| Date | Question | Answer |
|------|----------|--------|
| 2026-10-07 | Does the connector dialog of this account offer "Use your own OAuth client"? The documentation says so, it was not tried against claude.ai. | open, first real connection |
| 2026-10-07 | Does Anthropic's network (`160.79.104.0/21`) reach the proxy, including the `/.well-known/` paths? | open, first real connection |

## Tests

Written, status stays APPROVED until merged and deployed. `app/tests/test_mcp_oauth.py`, plus `test_config.py`. Also verified once against the official MCP Python client 2.3.0 with a pre-registered client: discovery, authorization with PKCE, login, token exchange and tool calls over a live server.

| Criterion | Tests |
|---|---|
| 1 | `test_req_005_protected_resource_metadata`, `test_req_005_authorization_server_metadata`, `test_req_005_unauthenticated_mcp_call_points_to_the_metadata`, `test_without_oauth_there_are_no_oauth_endpoints_and_no_pointer` |
| 2 | `test_req_005_unknown_client_or_redirect_never_redirects`, `test_redirect_allowed`, `test_loopback_redirect_with_any_port_is_accepted` |
| 3 | `test_req_005_bad_requests_redirect_with_an_error`, `test_req_005_bad_code_exchange_is_invalid_grant`, `test_pkce_matches_the_rfc_7636_test_vector`, `test_pkce_rejects_malformed_verifiers` |
| 4 | `test_req_005_correct_password_redirects_with_code_state_and_issuer`, `test_req_005_deny_redirects_with_access_denied_and_creates_nothing`, `test_req_005_tampered_form_is_rejected`, `test_req_005_expired_form_is_rejected`, `test_a_form_from_another_server_instance_is_rejected` |
| 5 | `test_req_005_wrong_password_gives_no_code_and_gets_slower`, `test_delay_is_capped_and_resets_after_a_successful_login`, `test_req_005_password_never_reaches_the_log` |
| 6 | `test_req_005_code_exchange_returns_a_token_pair_and_stores_only_hashes`, `test_req_005_a_replayed_code_fails_and_revokes_the_tokens_it_produced`, `test_an_expired_code_is_refused` |
| 7 | `test_req_005_refresh_rotates_the_refresh_token`, `test_req_005_reusing_a_rotated_refresh_token_revokes_the_whole_family`, `test_expired_refresh_token_is_invalid_grant`, `test_an_access_token_cannot_be_used_to_refresh` |
| 8 | `test_req_005_the_access_token_opens_the_mcp_endpoint`, `test_the_refresh_token_does_not_open_the_mcp_endpoint`, `test_req_005_expired_access_token_is_rejected`, `test_req_005_revoke_all_logs_everything_out`, `test_static_token_still_works_with_oauth_enabled`, `test_req_005_full_flow_from_login_to_reading_a_recording` |
| 9 | `test_client_secret_is_required_when_configured` |
| 10 | `test_req_005_unsafe_oauth_configuration_is_refused`, `test_any_single_oauth_setting_without_a_password_is_refused`, `test_oauth_alone_is_enough_to_start` |
| 11 | `test_host_names_may_be_pasted_as_urls`, `test_req_005_pasted_url_in_allowed_hosts_still_works` |
