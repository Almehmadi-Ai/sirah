"""Ingest layer: turn raw uploads into a flat list of candidate documents.

Each plain file = one document. A ZIP is exploded **safely** (no path traversal,
no absolute paths, caps enforced) and each supported entry becomes its own
document. By default 1 document = 1 candidate downstream (the most reliable
separation); multi-CV-in-one-PDF splitting is handled later in segmentation.

Archives are untrusted, so decompression is bounded twice (zip-bomb defence):

  1. ``check_archive`` validates the CENTRAL DIRECTORY before anything is
     extracted - entry count, each member's declared size, the archive's total
     declared size, and each member's compression ratio;
  2. extraction then STREAMS each member in chunks and enforces the same caps on
     the bytes actually produced, so a central directory that lies about sizes
     cannot get past step 1.

A Word ``.docx`` is itself a ZIP, so every ``.docx`` - uploaded directly or found
inside an archive - passes the same central-directory check before python-docx
opens it.

Files are keyed by a unique path and a content hash, never by their name: two
different CVs may both be called ``resume.pdf`` (and two archives may contain the
same member names), so a name is display-only.
"""

from __future__ import annotations

import hashlib
import os
import zipfile
import zlib
from collections.abc import Sequence
from typing import NamedTuple, TypedDict

SUPPORTED_DOC = (".pdf",)
SUPPORTED_OFFICE = (".docx",)  # Word (XML) - clean text layer, no OCR/VLM needed
SUPPORTED_IMG = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp")
SUPPORTED = SUPPORTED_DOC + SUPPORTED_OFFICE + SUPPORTED_IMG

_MB = 1024 * 1024
_CHUNK = 64 * 1024
# Compression ratios are only meaningful for sizeable members: a few KB of
# blank page can legitimately compress 1000:1 and is harmless.
_RATIO_FLOOR = _MB


class ArchiveRejected(ValueError):
    """An archive exceeds the decompression limits; the message is user-safe."""


class StoredUpload(NamedTuple):
    """A file from the request, saved under a unique path in the request's temp dir."""

    name: str  # original file name (display only)
    path: str
    sha256: str  # content hash: exact-duplicate detection and re-sent-file skipping


class IngestedFile(TypedDict):
    """One candidate document: an uploaded file or a member of an uploaded ZIP."""

    name: str  # display name
    path: str
    kind: str  # pdf | docx | image
    container: str | None  # the ZIP it came from, if any
    sha256: str


def kind_of(name: str) -> str:
    low = name.lower()
    if low.endswith(SUPPORTED_DOC):
        return "pdf"
    if low.endswith(SUPPORTED_OFFICE):
        return "docx"
    if low.endswith(SUPPORTED_IMG):
        return "image"
    return "unsupported"


def _safe_member(name: str) -> bool:
    """Reject path traversal, absolute paths, hidden macOS cruft, directories."""
    if not name or name.endswith("/"):
        return False
    norm = name.replace("\\", "/")
    if norm.startswith("/") or ".." in norm.split("/"):
        return False
    base = os.path.basename(norm)
    if not base or base.startswith(".") or "__MACOSX" in norm:
        return False
    return kind_of(base) != "unsupported"


# ---------------------------------------------------------------- limits
def _member_cap(settings) -> int:
    # a file inside an archive obeys the same cap as a file uploaded directly
    return settings.max_upload_mb * _MB


def _ratio_exceeded(produced: int, compressed: int, settings) -> bool:
    return produced > _RATIO_FLOOR and produced > settings.max_zip_ratio * max(compressed, 1)


def _check_infos(infos: list[zipfile.ZipInfo], label: str, settings) -> None:
    if len(infos) > settings.max_zip_entries:
        raise ArchiveRejected(
            f"{label}: {len(infos)} entries exceeds the limit of "
            f"{settings.max_zip_entries} (CV_MAX_ZIP_ENTRIES)"
        )
    member_cap, total_cap = _member_cap(settings), settings.max_zip_total_mb * _MB
    total = 0
    for info in infos:
        if info.file_size > member_cap:
            raise ArchiveRejected(
                f"{label}: a member expands to {info.file_size // _MB} MB, over the "
                f"{settings.max_upload_mb} MB per-file limit (CV_MAX_UPLOAD_MB)"
            )
        if _ratio_exceeded(info.file_size, info.compress_size, settings):
            raise ArchiveRejected(
                f"{label}: a member's compression ratio exceeds "
                f"{settings.max_zip_ratio}:1 (CV_MAX_ZIP_RATIO)"
            )
        total += info.file_size
        if total > total_cap:
            raise ArchiveRejected(
                f"{label}: expands to more than {settings.max_zip_total_mb} MB in total "
                f"(CV_MAX_ZIP_TOTAL_MB)"
            )


def check_archive(path: str, label: str, settings) -> None:
    """Validate an archive's central directory against the decompression limits
    BEFORE anything is extracted. Raises :class:`ArchiveRejected`. A file that
    is not a readable ZIP is not this check's concern (it simply yields no
    documents later)."""
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, OSError):
        return
    _check_infos(infos, label, settings)


def _extract_member(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, dest: str, budget: int, label: str, settings
) -> tuple[int, str]:
    """Stream one member to ``dest`` enforcing the caps on the bytes actually
    produced. Returns (bytes written, sha256); raises ArchiveRejected (after
    removing the partial file) if the member or the archive budget is exceeded."""
    member_cap = _member_cap(settings)
    written = 0
    digest = hashlib.sha256()
    try:
        with zf.open(info) as src, open(dest, "wb") as fh:
            while chunk := src.read(_CHUNK):
                written += len(chunk)
                if written > member_cap or written > budget:
                    raise ArchiveRejected(f"{label}: decompressed data exceeds the size limits")
                if _ratio_exceeded(written, info.compress_size, settings):
                    raise ArchiveRejected(
                        f"{label}: a member's compression ratio exceeds "
                        f"{settings.max_zip_ratio}:1 (CV_MAX_ZIP_RATIO)"
                    )
                digest.update(chunk)
                fh.write(chunk)
    except ArchiveRejected:
        if os.path.exists(dest):
            os.remove(dest)
        raise
    return written, digest.hexdigest()


# ---------------------------------------------------------------- expansion
def expand_uploads(uploads: Sequence[StoredUpload], dest_dir: str, settings) -> list[IngestedFile]:
    """Flatten the request's uploads (plain files and ZIPs) into candidate
    documents, in upload order, with caps from settings enforced. Raises
    :class:`ArchiveRejected` if an archive (or a .docx) exceeds the
    decompression limits."""
    out: list[IngestedFile] = []
    extract_root = os.path.join(dest_dir, "_unzipped")

    for n, up in enumerate(uploads):
        if up.name.lower().endswith(".zip"):
            # one directory per uploaded archive: two archives may share a name
            target = os.path.join(extract_root, f"{n:04d}")
            room = settings.max_files - len(out)
            out.extend(_explode_zip(up.name, up.path, target, settings, room))
        elif kind_of(up.name) != "unsupported":
            out.append(
                {
                    "name": os.path.basename(up.name),
                    "path": up.path,
                    "kind": kind_of(up.name),
                    "container": None,
                    "sha256": up.sha256,
                }
            )
        # silently skip other unsupported top-level files; caller reports the count
        if len(out) >= settings.max_files:
            out = out[: settings.max_files]
            break
    for doc in out:
        if doc["kind"] == "docx":
            check_archive(doc["path"], doc["name"], settings)
    return out


def _explode_zip(
    zip_name: str, zip_path: str, target: str, settings, limit: int
) -> list[IngestedFile]:
    """Extract up to ``limit`` supported members (the batch's remaining file
    slots) into ``target``."""
    found: list[IngestedFile] = []
    label = os.path.basename(zip_name)
    os.makedirs(target, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = zf.infolist()
            _check_infos(infos, label, settings)  # central directory, before extracting
            budget = settings.max_zip_total_mb * _MB
            for i, info in enumerate(infos):
                if len(found) >= limit:
                    break
                if not _safe_member(info.filename):
                    continue
                # unique per member: folders inside the archive may repeat a name
                dest = os.path.join(target, f"{i:04d}_{os.path.basename(info.filename)}")
                try:
                    written, sha256 = _extract_member(zf, info, dest, budget, label, settings)
                    budget -= written
                except (
                    zipfile.BadZipFile,  # CRC mismatch / truncated member
                    RuntimeError,  # encrypted member
                    NotImplementedError,  # unsupported compression method
                    EOFError,
                    zlib.error,
                    OSError,
                ):
                    continue  # one unreadable member must not sink the archive
                found.append(
                    {
                        "name": os.path.basename(info.filename),
                        "path": dest,
                        "kind": kind_of(info.filename),
                        "container": label,
                        "sha256": sha256,
                    }
                )
    except zipfile.BadZipFile:
        pass
    return found
