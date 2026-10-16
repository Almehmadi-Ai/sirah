"""Zip-bomb defence: archives are checked against decompression limits from the
central directory BEFORE extraction, and the caps are enforced again on the bytes
actually produced while streaming. All archives here are crafted in-memory and
stay small on disk (a few hundred KB at most)."""

import hashlib
import io
import os
import struct
import zipfile

import pytest

from app.config import get_settings
from app.pipeline import ingest

MB = 1024 * 1024


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("CV_LLM_MODE", "mock")
    for k in ("CV_MAX_UPLOAD_MB", "CV_MAX_ZIP_ENTRIES", "CV_MAX_ZIP_TOTAL_MB", "CV_MAX_ZIP_RATIO"):
        monkeypatch.delenv(k, raising=False)
    return get_settings()


def _zip(path, members, method=zipfile.ZIP_DEFLATED):
    with zipfile.ZipFile(path, "w", method) as z:
        for name, data in members:
            z.writestr(name, data)
    return str(path)


def _up(name, path):
    """A saved upload as the HTTP layer hands it to ingest."""
    with open(path, "rb") as f:
        return ingest.StoredUpload(name, str(path), hashlib.sha256(f.read()).hexdigest())


def _pdf_bytes(n=2048):
    return b"%PDF-1.4\n" + os.urandom(n)  # incompressible, like a real PDF stream


def test_legitimate_archive_is_extracted(tmp_path, settings):
    zp = _zip(tmp_path / "cvs.zip", [(f"cv_{i}.pdf", _pdf_bytes()) for i in range(3)])
    ingest.check_archive(zp, "cvs.zip", settings)  # no exception
    docs = ingest.expand_uploads([_up("cvs.zip", zp)], str(tmp_path), settings)
    assert [d["name"] for d in docs] == ["cv_0.pdf", "cv_1.pdf", "cv_2.pdf"]
    assert all(d["container"] == "cvs.zip" for d in docs)


def test_zip_bomb_is_rejected_from_the_central_directory(tmp_path, settings):
    zp = _zip(tmp_path / "bomb.zip", [("cv.pdf", b"\0" * (64 * MB))])
    assert os.path.getsize(zp) < 200 * 1024  # tiny on disk...
    with pytest.raises(ingest.ArchiveRejected, match="CV_MAX_UPLOAD_MB|CV_MAX_ZIP_RATIO"):
        ingest.check_archive(zp, "bomb.zip", settings)
    with pytest.raises(ingest.ArchiveRejected):
        ingest.expand_uploads([_up("bomb.zip", zp)], str(tmp_path), settings)
    # ...and nothing was decompressed before the decision
    unzipped = tmp_path / "_unzipped"
    assert not unzipped.exists() or not any(p.is_file() for p in unzipped.rglob("*"))


def test_high_compression_ratio_is_rejected(tmp_path, settings):
    # 4 MB of zeros is under every size cap; only the ratio gives it away
    zp = _zip(tmp_path / "ratio.zip", [("cv.pdf", b"\0" * (4 * MB))])
    with pytest.raises(ingest.ArchiveRejected, match="CV_MAX_ZIP_RATIO"):
        ingest.check_archive(zp, "ratio.zip", settings)


def test_too_many_entries_is_rejected(tmp_path, settings):
    zp = _zip(tmp_path / "many.zip", [(f"x{i}.txt", b"") for i in range(1001)])
    with pytest.raises(ingest.ArchiveRejected, match="CV_MAX_ZIP_ENTRIES"):
        ingest.check_archive(zp, "many.zip", settings)


def test_total_size_is_capped(tmp_path, monkeypatch):
    monkeypatch.setenv("CV_MAX_ZIP_TOTAL_MB", "1")
    s = get_settings()
    zp = _zip(
        tmp_path / "big.zip",
        [("a.pdf", _pdf_bytes(600 * 1024)), ("b.pdf", _pdf_bytes(600 * 1024))],
        method=zipfile.ZIP_STORED,
    )
    with pytest.raises(ingest.ArchiveRejected, match="CV_MAX_ZIP_TOTAL_MB"):
        ingest.check_archive(zp, "big.zip", s)


def test_per_member_size_is_capped(tmp_path, monkeypatch):
    monkeypatch.setenv("CV_MAX_UPLOAD_MB", "1")
    s = get_settings()
    zp = _zip(tmp_path / "one.zip", [("a.pdf", _pdf_bytes(2 * MB))], method=zipfile.ZIP_STORED)
    with pytest.raises(ingest.ArchiveRejected, match="CV_MAX_UPLOAD_MB"):
        ingest.check_archive(zp, "one.zip", s)


def _patch_declared_size(data: bytes, declared: int) -> bytes:
    """Make the local header AND central directory lie about the uncompressed size."""
    buf = bytearray(data)
    lh = buf.find(b"PK\x03\x04")
    struct.pack_into("<I", buf, lh + 22, declared)
    cd = buf.find(b"PK\x01\x02")
    struct.pack_into("<I", buf, cd + 24, declared)
    return bytes(buf)


def test_lying_central_directory_cannot_expand_past_its_declared_size(tmp_path, settings):
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("cv.pdf", b"\0" * (32 * MB))
    zp = tmp_path / "liar.zip"
    zp.write_bytes(_patch_declared_size(raw.getvalue(), 64 * 1024))
    ingest.check_archive(str(zp), "liar.zip", settings)  # the lie passes the CD check...
    docs = ingest.expand_uploads([_up("liar.zip", zp)], str(tmp_path), settings)
    assert docs == []  # ...but the member fails its CRC and is skipped
    written = [p.stat().st_size for p in (tmp_path / "_unzipped").rglob("*") if p.is_file()]
    assert max(written, default=0) <= 64 * 1024  # never more than declared


def test_streaming_enforces_the_budget_and_cleans_up(tmp_path, settings):
    zp = _zip(tmp_path / "s.zip", [("cv.pdf", _pdf_bytes(300 * 1024))])
    dest = tmp_path / "out.pdf"
    with zipfile.ZipFile(zp) as zf:
        info = zf.getinfo("cv.pdf")
        with pytest.raises(ingest.ArchiveRejected):
            ingest._extract_member(zf, info, str(dest), 100 * 1024, "s.zip", settings)
    assert not dest.exists()


def test_docx_bomb_is_rejected(tmp_path, settings):
    # a .docx is a ZIP too: python-docx must never inflate a hostile one
    docx = _zip(tmp_path / "cv.docx", [("word/document.xml", b"<w:t>a</w:t>" * (6 * MB))])
    with pytest.raises(ingest.ArchiveRejected):
        ingest.expand_uploads([_up("cv.docx", docx)], str(tmp_path), settings)


def test_extraction_stops_at_the_file_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("CV_MAX_FILES", "2")
    s = get_settings()
    zp = _zip(tmp_path / "five.zip", [(f"cv_{i}.pdf", _pdf_bytes()) for i in range(5)])
    docs = ingest.expand_uploads([_up("five.zip", zp)], str(tmp_path), s)
    assert len(docs) == 2
    assert len(list((tmp_path / "_unzipped").rglob("*.pdf"))) == 2  # not 5


def test_api_rejects_a_zip_bomb_upfront(api, tmp_path):
    zp = _zip(tmp_path / "bomb.zip", [("cv.pdf", b"\0" * (64 * MB))])
    with open(zp, "rb") as f:
        r = api.post("/sessions/bomb/analyze", files={"files": ("bomb.zip", f, "application/zip")})
    assert r.status_code == 413
    assert "upload rejected" in r.json()["detail"]
    assert api.get("/sessions/bomb/status").status_code == 404  # no session was started
