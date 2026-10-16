"""Shared fixtures for API-level tests: a fresh in-process app per test, in mock
mode (no model, no network), that never persists anything to disk."""

import time

import pytest
from fastapi.testclient import TestClient

API_KEY = "test-key"


@pytest.fixture
def api(monkeypatch):
    """TestClient for a new app instance: authenticated, mock backend, memory-only
    (each test gets its own session store, created by the app's lifespan)."""
    monkeypatch.setenv("CV_LLM_MODE", "mock")
    monkeypatch.setenv("CV_API_KEY", API_KEY)
    monkeypatch.setenv("CV_DATA_DIR", "")
    from app import main

    with TestClient(main.create_app()) as client:
        client.headers.update({"X-API-Key": API_KEY})
        yield client


@pytest.fixture
def wait_ready():
    """Poll a session's status until the background worker finishes."""

    def _wait(client, chat_id, timeout=20.0):
        st = {}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            st = client.get(f"/sessions/{chat_id}/status").json()
            if st.get("status") in ("ready", "error"):
                return st
            time.sleep(0.05)
        return st

    return _wait


@pytest.fixture
def make_docx():
    """Build a Word .docx (one paragraph per line) in memory."""

    def _make(lines) -> bytes:
        import io

        import docx

        d = docx.Document()
        for ln in lines:
            d.add_paragraph(ln)
        buf = io.BytesIO()
        d.save(buf)
        return buf.getvalue()

    return _make
