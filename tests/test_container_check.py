"""Regression tests for bounded startup polling; no network or sleeping."""

from http.client import RemoteDisconnected
from urllib.error import URLError

import pytest

from scripts import check_container


class Response:
    def __init__(self, body=b"ok", status=200):
        self.status = status
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self.body


@pytest.mark.parametrize("failure", [
    ConnectionResetError("startup reset"), RemoteDisconnected("startup disconnect"),
    URLError("startup unavailable"), TimeoutError("startup timeout"),
])
def test_transient_startup_failure_retries_then_passes(monkeypatch, failure):
    attempts = []
    sleeps = []

    def open_response(url, timeout):
        assert url == "http://127.0.0.1:7860/_stcore/health"
        assert timeout == 2
        attempts.append(url)
        if len(attempts) == 1:
            raise failure
        return Response()

    monkeypatch.setattr(check_container, "urlopen", open_response)
    monkeypatch.setattr(check_container.time, "sleep", sleeps.append)
    check_container.main()
    assert len(attempts) == 2
    assert sleeps == [1]


@pytest.mark.parametrize("mode", ["reset", "unhealthy_body", "unhealthy_status"])
def test_persistent_unhealthy_server_fails_after_bounded_attempts(monkeypatch, mode):
    attempts = []
    sleeps = []

    def open_response(*args, **kwargs):
        attempts.append(1)
        if mode == "reset":
            raise ConnectionResetError("persistent failure")
        return Response(body=b"not ready") if mode == "unhealthy_body" else Response(status=503)

    monkeypatch.setattr(check_container, "urlopen", open_response)
    monkeypatch.setattr(check_container.time, "sleep", sleeps.append)
    with pytest.raises(SystemExit, match="startup check failed"):
        check_container.main()
    assert len(attempts) == 40
    assert sleeps == [1] * 40
