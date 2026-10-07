"""
OAuth 2.1 authorization code flow with PKCE for the MCP server (REQ-005, D-012).

Data layer only: one-time codes, access tokens and rotating refresh tokens, all stored
as SHA-256 hashes so a copy of the database cannot be used as a login. Tokens belong
to a family (the code they came from). Replaying a code or an already used refresh
token revokes the whole family.
"""
import base64
import datetime as dt
import hashlib
import hmac
import logging
import re
import secrets

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from database import OAuthCode, OAuthToken
from utils import as_utc

log = logging.getLogger(__name__)

CODE_TTL = dt.timedelta(seconds=60)
ACCESS_TTL = dt.timedelta(hours=1)
REFRESH_TTL = dt.timedelta(days=30)
PURGE_AFTER = dt.timedelta(days=1)
VERIFIER_RE = re.compile(r"[A-Za-z0-9\-._~]{43,128}")
CHALLENGE_RE = re.compile(r"[A-Za-z0-9\-_]{43}")
# Bulk statements run in the database only: comparing timestamps in Python would mix the naive
# values SQLite returns with the aware ones used here.
BULK = {"synchronize_session": False}


class OAuthError(Exception):
    """An error with an RFC 6749 error code, returned as JSON by the token endpoint."""

    def __init__(self, error: str, description: str, status: int = 400):
        super().__init__(description)
        self.error, self.description, self.status = error, description, status


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def is_valid_challenge(challenge: str) -> bool:
    """An S256 code challenge is a base64url SHA-256 digest: exactly 43 characters."""
    return bool(CHALLENGE_RE.fullmatch(challenge or ""))


def pkce_matches(verifier: str, challenge: str) -> bool:
    if not VERIFIER_RE.fullmatch(verifier or ""):
        return False
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return hmac.compare_digest(digest, challenge)


def purge_expired(session: Session, now: dt.datetime) -> None:
    cutoff = now - PURGE_AFTER
    session.execute(delete(OAuthCode).where(OAuthCode.expires_at < cutoff), execution_options=BULK)
    session.execute(delete(OAuthToken).where(OAuthToken.expires_at < cutoff), execution_options=BULK)


def issue_code(session: Session, redirect_uri: str, code_challenge: str, now: dt.datetime) -> str:
    code = new_secret()
    session.add(OAuthCode(code_hash=_hash(code), redirect_uri=redirect_uri,
                          code_challenge=code_challenge, expires_at=now + CODE_TTL))
    session.commit()
    return code


def _revoke_family(session: Session, family_id: str, now: dt.datetime) -> None:
    session.execute(update(OAuthToken).where(OAuthToken.family_id == family_id,
                                             OAuthToken.revoked_at.is_(None)).values(revoked_at=now),
                    execution_options=BULK)
    session.commit()
    log.warning("OAuth token family revoked after reuse of a code or refresh token")


def _issue_pair(session: Session, family_id: str, now: dt.datetime) -> dict:
    access, refresh = new_secret(), new_secret()
    session.add(OAuthToken(token_hash=_hash(access), kind="access", family_id=family_id,
                           expires_at=now + ACCESS_TTL))
    session.add(OAuthToken(token_hash=_hash(refresh), kind="refresh", family_id=family_id,
                           expires_at=now + REFRESH_TTL))
    purge_expired(session, now)
    session.commit()
    return {"access_token": access, "token_type": "Bearer",
            "expires_in": int(ACCESS_TTL.total_seconds()), "refresh_token": refresh}


def exchange_code(session: Session, code: str, redirect_uri: str, verifier: str, now: dt.datetime) -> dict:
    code_hash = _hash(code or "")
    row = session.get(OAuthCode, code_hash)
    if row is None:
        raise OAuthError("invalid_grant", "unknown authorization code")
    if row.used_at is not None:
        _revoke_family(session, code_hash, now)
        raise OAuthError("invalid_grant", "authorization code already used")
    if as_utc(row.expires_at) <= now:
        raise OAuthError("invalid_grant", "authorization code expired")
    if row.redirect_uri != redirect_uri:
        raise OAuthError("invalid_grant", "redirect_uri does not match the authorization request")
    if not pkce_matches(verifier, row.code_challenge):
        raise OAuthError("invalid_grant", "PKCE verification failed")
    claimed = session.execute(update(OAuthCode).where(OAuthCode.code_hash == code_hash,
                                                      OAuthCode.used_at.is_(None)).values(used_at=now),
                              execution_options=BULK)
    if claimed.rowcount != 1:  # another request used it in the meantime
        session.rollback()
        raise OAuthError("invalid_grant", "authorization code already used")
    return _issue_pair(session, code_hash, now)


def refresh_tokens(session: Session, refresh_token: str, now: dt.datetime) -> dict:
    token_hash = _hash(refresh_token or "")
    row = session.get(OAuthToken, token_hash)
    if row is None or row.kind != "refresh" or row.revoked_at is not None or as_utc(row.expires_at) <= now:
        raise OAuthError("invalid_grant", "invalid refresh token")
    if row.used_at is not None:
        _revoke_family(session, row.family_id, now)
        raise OAuthError("invalid_grant", "refresh token already used")
    claimed = session.execute(update(OAuthToken).where(OAuthToken.token_hash == token_hash,
                                                       OAuthToken.used_at.is_(None)).values(used_at=now),
                              execution_options=BULK)
    if claimed.rowcount != 1:
        session.rollback()
        raise OAuthError("invalid_grant", "refresh token already used")
    return _issue_pair(session, row.family_id, now)


def validate_access_token(session: Session, token: str, now: dt.datetime) -> bool:
    row = session.scalar(select(OAuthToken).where(OAuthToken.token_hash == _hash(token or "")))
    return bool(row and row.kind == "access" and row.revoked_at is None and as_utc(row.expires_at) > now)


def revoke_all(session: Session, now: dt.datetime) -> int:
    result = session.execute(update(OAuthToken).where(OAuthToken.revoked_at.is_(None)).values(revoked_at=now),
                             execution_options=BULK)
    session.commit()
    return result.rowcount
