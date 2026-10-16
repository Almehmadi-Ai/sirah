"""End-to-end HTTP API flow in mock mode: the real service surface (multipart
ZIP upload -> ingest, async status polling, scoring, adverse-impact report,
reset). Exercises the paths a unit test cannot: routing, file handling and the
background worker - all with zero models. Authentication has its own module."""

import logging
import os

import pytest

from tests import make_fixtures


@pytest.fixture(scope="module")
def cvs_zip(tmp_path_factory):
    d = tmp_path_factory.mktemp("fixtures")
    make_fixtures.main(str(d))
    with open(os.path.join(d, "cvs.zip"), "rb") as f:
        return f.read()


def test_full_flow_analyze_status_score_audit_reset(api, wait_ready, cvs_zip):
    # 1. analyze a multipart ZIP (exercises ingest + async worker)
    r = api.post(
        "/sessions/flow/analyze",
        files={"files": ("cvs.zip", cvs_zip, "application/zip")},
    )
    assert r.status_code == 202

    # 2. poll status until the background worker publishes the batch
    st = wait_ready(api, "flow")
    assert st["status"] == "ready", f"analysis did not finish: {st}"
    assert st["candidate_count"] >= 2
    assert st["llm_mode"] == "mock"
    assert "Demo (mock) mode" in st["roster_markdown"]  # mock clearly labelled
    cids = [c["candidate_id"] for c in st["roster"]]
    assert len(cids) >= 2

    # 3. score against a JD -> matched/missing + ranking
    jd = "Recruiter\nRequired skills: Recruitment, Onboarding\nMinimum 3 years experience."
    sc = api.post("/sessions/flow/score", json={"jd_text": jd}).json()
    assert sc["scorable"] is True and sc["ranking"]
    assert sc["llm_mode"] == "mock"

    # 4a. audit with no body -> always returns the protected-leakage report
    a1 = api.post("/sessions/flow/audit", json={}).json()
    assert a1["leakage"]["clean"] is True  # no protected fields were extracted

    # 4b. audit with synthetic labels/selection -> four-fifths adverse-impact ratios.
    # group A selected, group B not -> adverse impact on B.
    labels = {cids[0]: {"grp": "A"}, cids[1]: {"grp": "B"}}
    selected = {cids[0]: True, cids[1]: False}
    a2 = api.post("/sessions/flow/audit", json={"labels": labels, "selected": selected}).json()
    ff = a2["adverse_impact"]
    assert ff["adverse_impact"] is True
    assert ff["axes"]["grp"]["groups"]["B"]["impact_ratio"] < 0.8

    # 5. reset drops the session (privacy)
    rr = api.post("/sessions/flow/reset")
    assert rr.status_code == 200 and rr.json()["reset"] is True
    gone = api.get("/sessions/flow/status")
    assert gone.status_code == 404


def test_unsupported_upload_is_handled(api, wait_ready):
    # a file with no supported CV content must not crash the worker
    r = api.post(
        "/sessions/bad/analyze",
        files={"files": ("note.txt", b"just some notes", "text/plain")},
    )
    assert r.status_code == 202
    st = wait_ready(api, "bad")
    assert st["status"] == "error" and "no supported CV files" in st["error"]


def test_audit_request_is_validated(api, wait_ready, cvs_zip):
    api.post("/sessions/aud/analyze", files={"files": ("cvs.zip", cvs_zip, "application/zip")})
    cids = [c["candidate_id"] for c in wait_ready(api, "aud")["roster"]]

    # no body / empty body -> the leakage report alone
    assert set(api.post("/sessions/aud/audit").json()) == {"chat_id", "leakage"}
    assert "adverse_impact" not in api.post("/sessions/aud/audit", json={}).json()
    # labels without the selection outcome (or vice versa) is a client error
    only_labels = {"labels": {cids[0]: {"grp": "A"}}}
    assert api.post("/sessions/aud/audit", json=only_labels).status_code == 422
    # a selection value that is not a boolean is rejected, not silently coerced
    bad = {"labels": {cids[0]: {"grp": "A"}}, "selected": {cids[0]: "maybe"}}
    assert api.post("/sessions/aud/audit", json=bad).status_code == 422
    # numeric group labels are accepted and compared as text
    ok = {
        "labels": {cids[0]: {"band": 1}, cids[1]: {"band": 2}},
        "selected": {cids[0]: True, cids[1]: False},
    }
    groups = api.post("/sessions/aud/audit", json=ok).json()["adverse_impact"]["axes"]["band"]
    assert set(groups["groups"]) == {"1", "2"}


def test_unknown_session_is_404_on_every_endpoint(api):
    for method, path in [
        ("get", "status"),
        ("get", "facts"),
        ("post", "audit"),
    ]:
        r = getattr(api, method)(f"/sessions/nobody/{path}")
        assert r.status_code == 404 and "no active session" in r.json()["detail"]
    assert api.post("/sessions/nobody/query", json={"question": "count"}).status_code == 404
    assert api.post("/sessions/nobody/score", json={"jd_text": "x"}).status_code == 404


def test_corrupt_documents_degrade_to_review_and_are_logged(api, wait_ready, caplog):
    files = [
        ("files", ("broken.pdf", b"%PDF-1.4\n%% not really a pdf", "application/pdf")),
        ("files", ("broken.docx", b"PK\x03\x04 not really a docx", "application/octet-stream")),
    ]
    with caplog.at_level(logging.INFO, logger="sirah"):
        assert api.post("/sessions/corrupt/analyze", files=files).status_code == 202
        st = wait_ready(api, "corrupt")
    assert st["status"] == "ready"
    assert [r["status"] for r in st["roster"]] == ["NEEDS_REVIEW", "NEEDS_REVIEW"]
    logs = "\n".join(r.getMessage() for r in caplog.records)
    assert '"event": "parse_error"' in logs  # observable, by error class only
    assert "not really" not in logs  # never the content
