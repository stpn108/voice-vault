"""Logging setup: request URLs with presigned credentials must not be logged."""
import logging

import httpx
import pytest

from utils import setup_logging


@pytest.fixture(autouse=True)
def restore_levels():
    names = ("httpx", "httpcore")
    saved = {n: logging.getLogger(n).level for n in names}
    yield
    for n, level in saved.items():
        logging.getLogger(n).setLevel(level)


@pytest.mark.parametrize("name", ["httpx", "httpcore"])
def test_http_client_loggers_are_quiet_at_info(monkeypatch, name):
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    setup_logging()
    assert logging.getLogger(name).getEffectiveLevel() == logging.WARNING


def test_presigned_url_is_not_logged_on_a_request(monkeypatch, caplog):
    # Bug seen in production: the S3 link with X-Amz-Security-Token appeared in the log.
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    setup_logging()
    url = "https://bucket.s3.amazonaws.com/file.json.gz?X-Amz-Security-Token=SECRET&X-Amz-Signature=SIG"
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])))
    with caplog.at_level(logging.INFO):
        client.get(url)
    assert "SECRET" not in caplog.text and "X-Amz-Signature" not in caplog.text
