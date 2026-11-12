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


@pytest.fixture
def clean_handlers():
    """setup_logging() adds file handlers to global loggers; remove and close them after each test."""
    loggers = [logging.getLogger(), logging.getLogger("access"), logging.getLogger("uvicorn")]
    saved = {lg: list(lg.handlers) for lg in loggers}
    yield
    for lg, handlers in saved.items():
        for handler in lg.handlers:
            if handler not in handlers:
                lg.removeHandler(handler)
                handler.close()


def _flush():
    for lg in (logging.getLogger(), logging.getLogger("access"), logging.getLogger("uvicorn")):
        for handler in lg.handlers:
            handler.flush()


def test_logging_writes_files_when_log_dir_set(monkeypatch, tmp_path, clean_handlers):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    setup_logging()
    setup_logging()  # every app module calls it; no second handler
    logging.getLogger("access").info('1.2.3.4 - - [x] "GET / HTTP/1.1" 200 5')
    logging.getLogger("some.module").info("only on stdout")
    logging.getLogger("some.module").warning("disk almost full")
    logging.getLogger("uvicorn.error").error("Exception in ASGI application")
    _flush()
    access = (tmp_path / "access.log").read_text().splitlines()
    errors = (tmp_path / "error.log").read_text()
    assert access == ['1.2.3.4 - - [x] "GET / HTTP/1.1" 200 5']
    assert "[WARNING] some.module: disk almost full" in errors
    assert "[ERROR] uvicorn.error: Exception in ASGI application" in errors
    assert "only on stdout" not in errors and "GET /" not in errors


@pytest.mark.parametrize("kind", ["missing", "not_a_directory"])
def test_logging_unwritable_log_dir_only_warns(monkeypatch, tmp_path, caplog, clean_handlers, kind):
    target = tmp_path / "nope"
    if kind == "not_a_directory":
        target.write_text("a file, not a directory")
    monkeypatch.setenv("LOG_DIR", str(target))
    with caplog.at_level(logging.WARNING):
        setup_logging()
    assert "Log files disabled" in caplog.text


def test_logging_without_log_dir_adds_no_file_handler(monkeypatch, clean_handlers):
    from logging.handlers import WatchedFileHandler
    monkeypatch.delenv("LOG_DIR", raising=False)
    setup_logging()
    assert not any(isinstance(h, WatchedFileHandler) for h in logging.getLogger().handlers)
