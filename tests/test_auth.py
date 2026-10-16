"""Authentication fails CLOSED: no configured key means no service, unless the
development-only CV_DISABLE_AUTH opt-out is set, which is logged loudly."""

import logging

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("CV_LLM_MODE", "mock")
    monkeypatch.setenv("CV_DATA_DIR", "")
    monkeypatch.delenv("CV_API_KEY", raising=False)
    monkeypatch.delenv("CV_DISABLE_AUTH", raising=False)
    return monkeypatch


def _app():
    from app import main

    return main.create_app()


def test_startup_refuses_without_an_api_key(env):
    from app.config import InsecureConfigError

    with pytest.raises(InsecureConfigError, match="CV_API_KEY"):
        with TestClient(_app()):
            pass


def test_requests_fail_closed_when_no_key_is_configured(env):
    # even if the startup guard were bypassed, nothing is served unauthenticated
    c = TestClient(_app())  # no lifespan
    r = c.post("/sessions/x/analyze", files={"files": ("cv.pdf", b"%PDF", "application/pdf")})
    assert r.status_code == 503
    assert c.get("/sessions/x/status").status_code == 503
    assert c.get("/health").status_code == 200  # liveness stays open


def test_dev_opt_out_runs_open_and_warns(env, caplog):
    env.setenv("CV_DISABLE_AUTH", "true")
    with caplog.at_level(logging.WARNING, logger="sirah"):
        with TestClient(_app()) as c:
            assert c.get("/sessions/none/status").status_code == 404  # accepted, no key
    assert any("authentication is DISABLED" in r.getMessage() for r in caplog.records)


def test_a_configured_key_always_wins_over_the_opt_out(env, caplog):
    env.setenv("CV_API_KEY", "real-key")
    env.setenv("CV_DISABLE_AUTH", "true")
    with caplog.at_level(logging.WARNING, logger="sirah"):
        with TestClient(_app()) as c:
            assert c.get("/sessions/none/status").status_code == 401
            ok = c.get("/sessions/none/status", headers={"X-API-Key": "real-key"})
            assert ok.status_code == 404
    assert any("CV_DISABLE_AUTH is ignored" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("sent", [None, "", "wrong", "real-key ", "REAL-KEY"])
def test_wrong_or_missing_key_is_rejected(env, sent):
    env.setenv("CV_API_KEY", "real-key")
    with TestClient(_app()) as c:
        headers = {} if sent is None else {"X-API-Key": sent}
        assert c.get("/sessions/x/status", headers=headers).status_code == 401


def test_non_ascii_key_is_compared_safely(env):
    env.setenv("CV_API_KEY", "مفتاح-سري")
    with TestClient(_app()) as c:
        assert c.get("/sessions/x/status", headers={"X-API-Key": "wrong"}).status_code == 401
