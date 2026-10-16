"""Smoke tests: the service imports cleanly, /health reports OCR + model
config, and session endpoints behave before any batch is uploaded."""


def test_health_ok(api):
    r = api.get("/health", headers={"X-API-Key": ""})  # open endpoint, no auth
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "sirah"
    assert body["status"] == "ok"
    assert "langs" in body["ocr"]
    assert body["models"]["llm"]
    assert "active" in body["sessions"]


def test_status_404_without_session(api):
    r = api.get("/sessions/chat-none/status")
    assert r.status_code == 404


def test_reset_is_idempotent(api):
    r = api.post("/sessions/chat-none/reset")
    assert r.status_code == 200
    assert r.json() == {"reset": False}


def test_analyze_rejects_empty_batch(api):
    # No supported files -> 4xx, not a crash.
    r = api.post("/sessions/chat-x/analyze", files={}, data={})
    assert r.status_code in (400, 415, 422)
