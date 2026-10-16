"""Uploads are keyed by position and content hash, never by file name: different
CVs that share a name are all analysed, and true duplicates are recognised by
their content - with or without contact details."""

import io
import zipfile

import pytest

from tests import make_fixtures

NO_CONTACT_CV = [
    "Rana Aziz",
    "Operations Analyst",
    "EXPERIENCE",
    "Operations Analyst, Example Company (2019 - 2024)",
    "Built weekly capacity reports for the operations team.",
    "EDUCATION",
    "BSc Business, Demo University, 2019",
    "SKILLS",
    "Excel, SQL, Reporting",
]


@pytest.fixture(scope="module")
def cvs(tmp_path_factory):
    d = tmp_path_factory.mktemp("fixtures")
    make_fixtures.main(str(d))
    return {
        "ahmed": (d / "cv_ahmed.pdf").read_bytes(),
        "sara": (d / "cv_sara.pdf").read_bytes(),
        "layla": (d / "cv_layla.pdf").read_bytes(),
    }


def _analyze(api, wait_ready, cid, files):
    assert api.post(f"/sessions/{cid}/analyze", files=files).status_code == 202
    st = wait_ready(api, cid)
    assert st["status"] == "ready", st
    return st


def test_different_cvs_with_the_same_name_are_all_kept(api, wait_ready, cvs):
    files = [
        ("files", ("resume.pdf", cvs["ahmed"], "application/pdf")),
        ("files", ("resume.pdf", cvs["sara"], "application/pdf")),
    ]
    st = _analyze(api, wait_ready, "same-name", files)
    assert st["candidate_count"] == 2
    assert {r["name"] for r in st["roster"]} == {"Ahmed Ali", "Sara Khan"}
    assert all(r["source"] == "resume.pdf" for r in st["roster"])
    assert all(r["duplicate_of"] is None and "DUPLICATE" not in r["flags"] for r in st["roster"])
    assert st["summary"]["duplicates"] == 0


def test_identical_content_is_an_exact_duplicate_whatever_its_name(api, wait_ready, cvs):
    files = [
        ("files", ("ahmed.pdf", cvs["ahmed"], "application/pdf")),
        ("files", ("ahmed (copy).pdf", cvs["ahmed"], "application/pdf")),
    ]
    st = _analyze(api, wait_ready, "copies", files)
    first, second = st["roster"]
    assert first["duplicate_of"] is None
    assert second["duplicate_of"] == first["candidate_id"] and "DUPLICATE" in second["flags"]
    assert st["summary"]["duplicates"] == 1


def test_a_cv_without_contact_details_is_still_caught_as_a_copy(api, wait_ready, make_docx):
    doc = make_docx(NO_CONTACT_CV)
    files = [
        ("files", ("rana.docx", doc, "application/octet-stream")),
        ("files", ("rana_final.docx", doc, "application/octet-stream")),
    ]
    st = _analyze(api, wait_ready, "no-contact", files)
    assert st["roster"][1]["duplicate_of"] == st["roster"][0]["candidate_id"]


def _zip(members) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members:
            z.writestr(name, data)
    return buf.getvalue()


def test_same_member_names_across_folders_and_archives_are_all_kept(api, wait_ready, cvs):
    batch_a = _zip([("team1/cv.pdf", cvs["ahmed"]), ("team2/cv.pdf", cvs["sara"])])
    batch_b = _zip([("team1/cv.pdf", cvs["layla"])])
    files = [
        ("files", ("batch.zip", batch_a, "application/zip")),
        ("files", ("batch.zip", batch_b, "application/zip")),
    ]
    st = _analyze(api, wait_ready, "zips", files)
    assert sorted(r["name"] for r in st["roster"]) == ["Ahmed Ali", "Layla Hassan", "Sara Khan"]
    assert st["summary"]["duplicates"] == 0


def test_oversized_upload_is_rejected_while_streaming(api, monkeypatch):
    monkeypatch.setenv("CV_MAX_UPLOAD_MB", "1")
    big = b"%PDF-1.4\n" + b"0" * (2 * 1024 * 1024)
    r = api.post("/sessions/big/analyze", files={"files": ("big.pdf", big, "application/pdf")})
    assert r.status_code == 413 and "too large" in r.json()["detail"]


def test_docx_is_supported():
    from app.pipeline.ingest import SUPPORTED, kind_of

    assert kind_of("CV1_Ahmed_AlOtaibi.docx") == "docx"
    assert kind_of("resume.PDF") == "pdf"
    assert kind_of("answer_key.xlsx") == "unsupported"  # spreadsheets are not CVs
    assert ".docx" in SUPPORTED
