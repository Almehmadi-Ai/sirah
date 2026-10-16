"""The privacy startup guard: every endpoint that receives candidate data must be
local, locality is decided by PARSING the URL (never substring matching), and
the only way past the guard is an explicit, loudly-logged opt-out."""

import dataclasses
import logging
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from app import config
from app.config import is_local_endpoint

LOCAL = [
    "http://localhost:11434",
    "http://LOCALHOST:11434",
    "http://localhost.:11434",
    "https://localhost",
    "http://user:secret@localhost:11434",
    "http://127.0.0.1:11434",
    "http://127.0.0.2:11434",
    "http://[::1]:11434",
    "http://[::ffff:127.0.0.1]:11434",
    "http://host.docker.internal:11434",  # the docker-compose default
    "http://ollama:11434",  # a Compose service name
    "http://172.17.0.1:11434",  # Docker bridge gateway (host-gateway on Linux)
    "http://10.0.0.5:11434",
    "http://192.168.1.20:11434",
    "http://[fd00::1]:11434",
]

REMOTE = [
    "http://localhost.attacker.net:11434",
    "http://127.0.0.1.nip.io:11434",
    "http://user@evil.com",
    "http://localhost@evil.com",
    "http://127.0.0.1@evil.com:11434",
    "http://host.docker.internal.evil.com",
    "https://api.example.com/v1",
    "http://8.8.8.8:11434",
    "http://[2001:4860:4860::8888]:11434",
    "http://169.254.169.254/latest/meta-data",  # link-local cloud metadata
    "http://134744072:11434",  # 8.8.8.8 written as an integer
    "http://0x7f000001:11434",  # numeric shorthands are refused, not resolved
    "http://[fe80::1%25eth0]:11434",  # link-local IPv6
    "ftp://localhost",
    "localhost:11434",  # no scheme
    "http://",
    "http://localhost:99999",  # malformed port
    "http://[::1",
    "",
]


@pytest.mark.parametrize("url", LOCAL)
def test_local_endpoints_are_accepted(url):
    assert is_local_endpoint(url)


@pytest.mark.parametrize("url", REMOTE)
def test_remote_or_malformed_endpoints_are_refused(url):
    assert not is_local_endpoint(url)


def test_every_url_setting_is_guarded():
    url_fields = {f.name for f in dataclasses.fields(config.Settings) if f.name.endswith("_url")}
    assert url_fields == set(config.OUTBOUND_ENDPOINTS)


def test_every_endpoint_used_in_app_code_is_guarded():
    # Any `settings.<x>_url` / `s.<x>_url` read in the app is an outbound
    # endpoint; it must be in the registry the startup guard enforces.
    app_dir = pathlib.Path(config.__file__).parent
    used = set()
    for path in app_dir.rglob("*.py"):
        used |= set(re.findall(r"\b(?:settings|s)\.(\w+_url)\b", path.read_text(encoding="utf-8")))
    assert used and used <= set(config.OUTBOUND_ENDPOINTS)


def _start(monkeypatch, **env):
    monkeypatch.setenv("CV_LLM_MODE", "mock")
    monkeypatch.setenv("CV_API_KEY", "k")
    monkeypatch.setenv("CV_DATA_DIR", "")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from app import main

    with TestClient(main.create_app()) as c:
        return c.get("/health").json()


def test_startup_refuses_a_remote_model_endpoint(monkeypatch):
    with pytest.raises(config.RemoteEndpointError, match="CV_OLLAMA_URL"):
        _start(monkeypatch, CV_OLLAMA_URL="http://localhost.attacker.net:11434")


def test_startup_refuses_even_in_mock_mode(monkeypatch):
    # the URL is the risk: flipping CV_LLM_MODE must not be the only safeguard
    with pytest.raises(config.RemoteEndpointError):
        _start(monkeypatch, CV_OLLAMA_URL="https://api.example.com")


def test_startup_accepts_the_compose_default(monkeypatch):
    health = _start(monkeypatch, CV_OLLAMA_URL="http://host.docker.internal:11434")
    assert health["privacy"] == {"endpoints_local": True, "remote_endpoints_allowed": False}


def test_explicit_opt_out_starts_and_warns_loudly(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="sirah"):
        health = _start(
            monkeypatch,
            CV_OLLAMA_URL="https://user:token@gpu.example.net:11434",
            CV_ALLOW_REMOTE_ENDPOINTS="true",
        )
    assert health["privacy"] == {"endpoints_local": False, "remote_endpoints_allowed": True}
    warning = "\n".join(r.getMessage() for r in caplog.records)
    assert "PRIVACY WARNING" in warning and "CV_OLLAMA_URL" in warning
    assert "gpu.example.net" in warning
    assert "token" not in warning  # credentials in the URL are never logged


def test_legacy_settings_are_reported_as_ignored(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="sirah"):
        _start(monkeypatch, CV_VISION_OCR_URL="http://localhost:9000")
    assert any("CV_VISION_OCR_URL is no longer used" in r.getMessage() for r in caplog.records)
