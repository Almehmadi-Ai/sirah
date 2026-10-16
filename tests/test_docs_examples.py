"""The README's example and docs/example-output.md are executable claims: this
module re-runs them against the real service and fails if either drifts from
what the service actually produces."""

import importlib.util
import json
import os
import re
import time

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.join(os.path.dirname(__file__), "..")
GEN = os.path.join(ROOT, "scripts", "generate_synthetic_data.py")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


README = _read("README.md")
EXAMPLE = _read("docs", "example-output.md")


def _readme_jd() -> str:
    m = re.search(r"-d '(\{\"jd_text\":.*?\})'", README)
    assert m, "README API example no longer posts a jd_text"
    return json.loads(m.group(1))["jd_text"]


def _example_audit_request() -> dict:
    blocks = re.findall(r"```json\n(.*?)\n```", EXAMPLE, flags=re.S)
    return json.loads(next(b for b in blocks if '"labels"' in b and '"selected"' in b))


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """Generate the corpus and replay the README flow: analyze the English demo
    bundle, score it with the README's JD, audit it with the documented body."""
    corpus = tmp_path_factory.mktemp("corpus")
    spec = importlib.util.spec_from_file_location("gen", GEN)
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    gen.main(str(corpus))

    mp = pytest.MonkeyPatch()
    mp.setenv("CV_LLM_MODE", "mock")
    mp.setenv("CV_API_KEY", "k")
    mp.setenv("CV_DATA_DIR", "")
    from app import main

    try:
        with TestClient(main.create_app()) as c:
            c.headers.update({"X-API-Key": "k"})
            with open(corpus / "bundles" / "demo_english.zip", "rb") as f:
                files = {"files": ("demo_english.zip", f.read(), "application/zip")}
            assert c.post("/sessions/demo/analyze", files=files).status_code == 202
            for _ in range(400):
                status = c.get("/sessions/demo/status").json()
                if status["status"] == "ready":
                    break
                time.sleep(0.05)
            score = c.post("/sessions/demo/score", json={"jd_text": _readme_jd()}).json()
            audit = c.post("/sessions/demo/audit", json=_example_audit_request()).json()
    finally:
        mp.undo()
    return {"status": status, "score": score, "audit": audit}


def test_readme_example_reaches_the_advertised_top_score(run):
    top = run["score"]["ranking"][0]
    assert (top["name"], top["score"], top["level"]) == ("David Kim", 100, "strong")


def test_readme_table_matches_the_real_ranking(run):
    rows = re.findall(
        r"^\| (\d+) \| (\d+) \| \*\*(.+?)\*\* \| `[^`]*` (\d+)% \| (\w+) \|", README, flags=re.M
    )
    documented = [(int(r), int(cv), name, int(s), lvl.lower()) for r, cv, name, s, lvl in rows]
    actual = [
        (r["rank"], r["cv"], r["name"], r["score"], r["level"]) for r in run["score"]["ranking"]
    ]
    assert documented == actual
    # ...and the injected CV is shown as held for review, exactly as the README says
    assert "| — | 7 | *Morgan Lee* | *not ranked* | Needs review |" in README
    assert "**Morgan Lee** (CV #7)" in run["score"]["report_md"]
    assert "Morgan Lee" not in {r["name"] for r in run["score"]["ranking"]}


def test_example_output_page_is_verbatim_service_output(run):
    actual = run["status"]["roster_markdown"] + "\n" + run["score"]["report_md"]
    quoted = [ln[2:] for ln in EXAMPLE.splitlines() if ln.startswith("> ") and ln[2:].strip()]
    assert len(quoted) > 20
    missing = [ln for ln in quoted if ln not in actual]
    assert not missing, f"docs/example-output.md drifted from the service: {missing[:3]}"
    assert f'{{"jd_text": "{_readme_jd()}"}}' in EXAMPLE  # same JD as the README


def test_example_output_audit_matches_the_service(run):
    blocks = re.findall(r"```json\n(.*?)\n```", EXAMPLE, flags=re.S)
    documented = json.loads(next(b for b in blocks if '"adverse_impact"' in b))
    assert documented["adverse_impact"] == run["audit"]["adverse_impact"]
    assert documented["leakage"]["clean"] is run["audit"]["leakage"]["clean"] is True
