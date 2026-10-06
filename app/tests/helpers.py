"""Shared test helpers (not fixtures)."""
import base64
import json

import httpx

from plaud_auth import StoredTokens


def make_jwt(exp, iat=None, **claims):
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    payload = {"exp": exp, **claims}
    if iat is not None:
        payload["iat"] = iat
    return f"{b64({'alg': 'HS256'})}.{b64(payload)}.sig"


class MemoryStore:
    """In-memory TokenStore."""

    def __init__(self, tokens=None):
        self.tokens = tokens
        self.saves = 0

    def load(self):
        return self.tokens

    def save(self, tokens: StoredTokens):
        self.tokens = tokens
        self.saves += 1


def refresh_response(access, refresh=None, clearing=True):
    """Plaud's refresh answer: cookies only, a clearing cookie first."""
    headers = []
    if clearing:
        headers.append(("set-cookie", 'pld_ut=""; Domain=.plaud.ai; Max-Age=0; Path=/'))
    headers.append(("set-cookie", f"pld_ut={access}; Domain=.plaud.ai; Max-Age=86400; Path=/"))
    if refresh:
        headers.append(("set-cookie", f"pld_urt={refresh}; Domain=.plaud.ai; Path=/"))
    return httpx.Response(200, headers=headers, json={"status": 0})
