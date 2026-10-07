"""Shared test helpers (not fixtures)."""
import base64
import json

import httpx

from plaud_auth import StoredTokens


def make_jwt(exp, iat=None, header_typ=None, **claims):
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    payload = {"exp": exp, **claims}
    if iat is not None:
        payload["iat"] = iat
    header = {"alg": "HS256"}
    if header_typ is not None:
        header["typ"] = header_typ
    return f"{b64(header)}.{b64(payload)}.sig"


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


def add_recording(session, index=0, started=None, segments=3, **overrides):
    """Insert one stored recording with segments; returns the Recording."""
    import datetime as dt
    from database import Recording, Segment
    started = started or dt.datetime(2026, 10, 1, 8, 0, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=index)
    values = dict(
        plaud_id=f"p{index}", title=f"Title {index}", started_at=started,
        ended_at=started + dt.timedelta(minutes=5), duration_ms=300_000, summary=f"Summary {index}",
        segment_count=segments, content_hash=f"h{index}", is_plaud_processed=True,
        last_changed_at=started, verified_hash=f"h{index}", verified_at=started,
    )
    values.update(overrides)
    rec = Recording(**values)
    session.add(rec)
    session.flush()
    session.add_all(
        Segment(recording_id=rec.id, idx=i, speaker=f"Speaker {i % 2}", start_ms=i * 61_000,
                end_ms=i * 61_000 + 900, text=f"text {index}-{i}")
        for i in range(segments)
    )
    session.commit()
    return rec


def add_recording_on(session, *days, hour=10):
    """One recording per given local day (a date or ISO string), starting at `hour` UTC. Returns the ids."""
    import datetime as dt
    ids = []
    for i, day in enumerate(days):
        day = dt.date.fromisoformat(day) if isinstance(day, str) else day
        started = dt.datetime.combine(day, dt.time(hour), tzinfo=dt.timezone.utc)
        ids.append(add_recording(session, index=100 + i, started=started).id)
    return ids
