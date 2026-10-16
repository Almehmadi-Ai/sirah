"""Storage is ephemeral by default and opt-in otherwise: nothing touches disk at
import time, nothing is written anywhere unless CV_DATA_DIR is set, and when it
is set the same TTL / reset rules erase what was written."""

import os

import pytest
from fastapi.testclient import TestClient

KEY = "test-key"
JD = "Data Engineer\nRequired skills: Python, SQL\nMinimum 2 years of experience."
CV = [
    "Sam Okafor",
    "Data Engineer",
    "Mobile: +1 202 555 0150",
    "EXPERIENCE",
    "Data Engineer, Example Company (2018 - 2024)",
    "Built batch pipelines for the analytics warehouse.",
    "EDUCATION",
    "BSc Computer Science, Demo University, 2017",
    "SKILLS",
    "Python, SQL, Airflow",
]


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("CV_LLM_MODE", "mock")
    monkeypatch.setenv("CV_API_KEY", KEY)
    monkeypatch.delenv("CV_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)  # any stray relative write would land here
    return monkeypatch


def _files(root) -> set[str]:
    return {os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs}


def _client():
    from app import main

    client = TestClient(main.create_app())
    client.headers.update({"X-API-Key": KEY})
    return client


def _analyze_and_score(client, cid, wait_ready, docx):
    files = {"files": ("cv.docx", docx, "application/octet-stream")}
    assert client.post(f"/sessions/{cid}/analyze", files=files).status_code == 202
    assert wait_ready(client, cid)["status"] == "ready"
    assert client.post(f"/sessions/{cid}/score", json={"jd_text": JD}).json()["scorable"]


def test_default_is_memory_only(env):
    from app.config import get_settings

    assert get_settings().data_dir == ""


def test_building_the_app_has_no_side_effects(env, tmp_path):
    target = tmp_path / "data"
    env.setenv("CV_DATA_DIR", str(target))
    client = _client()
    assert not target.exists()  # nothing created at import / construction time
    with client:
        assert (target / "sessions").is_dir()  # created when the app STARTS


def test_nothing_is_written_to_disk_by_default(env, tmp_path, wait_ready, make_docx):
    before = _files(tmp_path)
    with _client() as c:
        _analyze_and_score(c, "eph", wait_ready, make_docx(CV))
    assert _files(tmp_path) == before


def test_opt_in_persistence_survives_a_restart_and_reset_erases_it(
    env, tmp_path, wait_ready, make_docx
):
    data = tmp_path / "data"
    env.setenv("CV_DATA_DIR", str(data))
    with _client() as c:
        _analyze_and_score(c, "keep", wait_ready, make_docx(CV))
    assert (data / "sessions" / "keep.json").is_file()
    assert any((data / "rubrics").iterdir())  # JD-derived rubric cache (no CV content)

    with _client() as c:  # a restarted service reloads the batch...
        assert c.get("/sessions/keep/status").json()["status"] == "ready"
        assert c.post("/sessions/keep/reset").json() == {"reset": True}
    assert not (data / "sessions" / "keep.json").exists()  # ...and reset erases it


def test_calibration_history_needs_a_data_dir(env, tmp_path):
    from app.config import get_settings
    from app.scoring import calibration

    before = _files(tmp_path)
    out = calibration.record_and_check_drift(get_settings(), {"accuracy": 0.9, "kappa": 0.8})
    assert out["runs_logged"] == 1 and out["drift_alert"] is False
    assert _files(tmp_path) == before
