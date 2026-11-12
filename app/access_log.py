"""
Access log for the HTTP services (D-070): one line per request through the logger "access".

Pure ASGI middleware, no framework dependency. Logs the path and the Referer without the query string (OAuth
codes and state travel there) and never bodies, cookies, Authorization headers or form data. It reads
the peer address from the ASGI scope and does not change it or any proxy header.
"""
import logging
import os
import socket
import time

from utils import now_utc, LOCAL_TZ

access_log = logging.getLogger("access")

DEFAULT_SKIP = "/health,/healthz"


def _header(headers: list, name: bytes) -> str:
    for key, value in headers:
        if key.lower() == name:
            text = value.decode("latin-1")
            return text.replace("\\", "\\\\").replace('"', '\\"') if text else "-"
    return "-"


def _without_query(url: str) -> str:
    """A Referer can carry the same OAuth query string as the path."""
    return url.split("?", 1)[0].split("#", 1)[0] or "-"


def _printable(text: str) -> str:
    """Control characters would let a client forge extra log lines."""
    return "".join(ch if ch.isprintable() else "?" for ch in text)


class AccessLogMiddleware:
    def __init__(self, app, skip: str | None = None, service: str | None = None):
        self.app = app
        raw = os.getenv("ACCESS_LOG_SKIP", DEFAULT_SKIP) if skip is None else skip
        self.skip = {p.strip() for p in raw.split(",") if p.strip()}
        self.service = service or os.getenv("SERVICE_NAME") or socket.gethostname()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path", "") in self.skip:
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        state = {"status": 500, "bytes": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
            elif message["type"] == "http.response.body":
                state["bytes"] += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            access_log.info(self._line(scope, state, time.perf_counter() - start))

    def _line(self, scope, state: dict, seconds: float) -> str:
        headers = scope.get("headers") or []
        client = scope.get("client")
        peer = client[0] if client else "-"
        stamp = now_utc().astimezone(LOCAL_TZ).strftime("%d/%b/%Y:%H:%M:%S %z")
        request = f'{scope.get("method", "-")} {scope.get("path", "")} HTTP/{scope.get("http_version", "1.1")}'
        return _printable(
            f'{peer} - - [{stamp}] "{request}" {state["status"]} {state["bytes"]} '
            f'"{_without_query(_header(headers, b"referer"))}" "{_header(headers, b"user-agent")}" '
            f'xff="{_header(headers, b"x-forwarded-for")}" rt={seconds:.3f} svc={self.service}'
        )
