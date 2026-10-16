"""Analysis orchestration — everything between an HTTP request and the pipeline.

``app/main.py`` only authenticates, validates input and maps outcomes to HTTP;
this module owns the rest:

  * the session lifecycle: a fresh batch vs an append, and skipping files a chat
    UI re-sends on every turn (by content hash);
  * the background analysis worker (phase-parallel acquire -> VLM -> extract),
    duplicate detection over the whole merged batch, and publication;
  * the read models built from a session (summary, roster, facts, audit,
    query, score), each tagged with the active backend mode.

Analysis gaps are results, not errors: only infra / input failures surface as
exceptions.
"""

from __future__ import annotations

import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import fairness, logging_safe
from .config import Settings
from .models import AuditRequest, ScoreRequest
from .pipeline import acquire, dedup, extract, ingest, segment
from .pipeline.ingest import IngestedFile, StoredUpload
from .profiles import CandidateProfile
from .query import engine, render
from .reasons import profile_flags
from .scoring import rubric as scoring_rubric
from .scoring import service as scoring_service
from .session_store import Session, SessionStore


class SessionNotFound(LookupError):
    """No active session for this chat (never created, reset, or expired)."""

    def __init__(self) -> None:
        super().__init__("no active session for this chat (upload CVs first, or it expired)")


_NO_SUPPORTED_FILES = (
    "no supported CV files found (PDF, Word .docx, or image, optionally inside a ZIP)"
)

_DEMO_BANNER = (
    "> 🧪 **Demo (mock) mode** — these results are deterministic stand-ins, not "
    "real model inference. Set `CV_LLM_MODE=ollama` to use local models.\n\n"
)


# ---------------------------------------------------------------- read models
def batch_summary(profiles: list[CandidateProfile]) -> dict[str, int]:
    """Deterministic, guaranteed-DISJOINT KPI partition for any batch.

    Duplicate takes priority over needs-review, so a candidate is counted in
    exactly ONE category. By construction:
        processed + needs_review + duplicates == candidate_count
    This is the single source of truth for the KPI: clients display these
    numbers instead of recounting.
    """
    total = len(profiles)
    duplicates = sum(1 for p in profiles if p["duplicate_of"])
    needs_review = sum(
        1 for p in profiles if p["extraction"]["status"] == "NEEDS_REVIEW" and not p["duplicate_of"]
    )
    processed = total - duplicates - needs_review
    return {
        "candidate_count": total,
        "processed": processed,
        "needs_review": needs_review,
        "duplicates": duplicates,
        "ready": total - needs_review,  # legacy field, kept for compatibility
    }


def roster(profiles: list[CandidateProfile]) -> list[dict[str, Any]]:
    out = []
    for p in profiles:
        ident = p["identity"]
        out.append(
            {
                "no": p["display_no"],
                "candidate_id": p["candidate_id"],
                "name": ident["full_name"]["value"] or "(name not found)",
                "headline": p.get("headline"),
                "years": p["total_years_experience"]["value"],
                "top_skills": [s["name"] for s in p["skills"][:6]],
                "email": ident["emails"][0] if ident["emails"] else None,
                "status": p["extraction"]["status"],
                "flags": profile_flags(p),
                "duplicate_of": p["duplicate_of"],
                "source": p["source"]["file"],
            }
        )
    return out


def with_mode(payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Tag a response with the active backend mode and, in mock mode, prefix any
    rendered markdown with a clear demo banner so mock output is never mistaken
    for real model inference."""
    payload["llm_mode"] = settings.llm_mode
    if settings.llm_mode == "mock":
        for k in ("roster_markdown", "report_md"):
            if payload.get(k):
                payload[k] = _DEMO_BANNER + payload[k]
    return payload


# ---------------------------------------------------------------- batch processing
def _log(event: str, session: Session, t0: float | None = None, **fields: Any) -> None:
    """A PII-free log line for this session's job, timed from ``t0`` if given."""
    if t0 is not None:
        fields["duration_ms"] = (time.monotonic() - t0) * 1000
    logging_safe.log_event(event, session_id=session.chat_id, job_id=session.job_id, **fields)


def _unreadable_document(doc: IngestedFile) -> dict[str, Any]:
    return {
        "file": doc["name"],
        "kind": doc["kind"],
        "page_count": 0,
        "pages": [],
        "ocr_page_count": 0,
        "vlm_page_count": 0,
        "mean_ocr_confidence": None,
        "text_chars": 0,
        "empty": True,
        "unreadable": True,
        "sha256": doc["sha256"],
    }


def _process_documents(
    session: Session,
    uploads: list[StoredUpload],
    tmp: str,
    settings: Settings,
    base_no: int = 0,
) -> tuple[list[dict[str, Any]], list[CandidateProfile]] | None:
    """Acquire, rescue and extract a batch in three phases, scheduled around the
    single local GPU:

      phase 1  CPU  - text layer + OCR for every document in parallel
                      (``acquire_workers`` threads; no GPU use)
      phase 2  GPU  - VLM rescue of the pages OCR could not read, one document
                      at a time, so the vision model never competes with the
                      extraction model (interleaved, rescues time out)
      phase 3  GPU  - LLM extraction, ``extract_workers`` candidates at a time

    Candidates are numbered base_no+1..base_no+N in upload order. Returns
    (documents, profiles) in that order, or None when the upload contained no
    supported files."""
    docs_in = ingest.expand_uploads(uploads, tmp, settings)
    if not docs_in:
        return None
    n = len(docs_in)

    # phase 1: CPU-parallel acquisition (VLM deferred)
    session.progress = {"done": 0, "total": n, "stage": "reading"}
    documents: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(max(1, settings.acquire_workers), n)) as ex:
        futs = [
            ex.submit(acquire.acquire_document, d["name"], d["path"], d["kind"], settings, True)
            for d in docs_in
        ]
        for i, f in enumerate(futs):
            try:
                doc = f.result()
                doc["sha256"] = docs_in[i]["sha256"]  # carried to the profile's source
                documents.append(doc)
            # Boundary: third-party parsers (pdfminer, pypdf, PDFium, Pillow,
            # python-docx, Tesseract) raise many undocumented exception types on
            # malformed uploads. One bad file must not kill the batch: it becomes
            # an "unreadable" candidate a human sees, and the failure is logged.
            except Exception as e:  # noqa: BLE001
                _log("document_error", session, error_type=type(e).__name__, stage="reading")
                documents.append(_unreadable_document(docs_in[i]))
            session.progress["done"] = len(documents)

    # phase 2: grouped VLM rescue (vision model resident once, no competition)
    pending = [i for i, d in enumerate(documents) if d.get("_vlm_pending")]
    if pending:
        session.progress = {"done": 0, "total": len(pending), "stage": "deep-reading"}
        for k, i in enumerate(pending):
            documents[i] = acquire.apply_vlm_rescue(documents[i], settings)
            session.progress["done"] = k + 1
    for d in documents:
        d.pop("_vlm_pending", None)
        d.pop("_path", None)

    # phase 3: extraction
    candidates = segment.documents_to_candidates(documents)
    session.progress = {"done": 0, "total": len(candidates), "stage": "extracting"}
    profiles: list[CandidateProfile] = []
    workers = max(1, min(settings.extract_workers, len(candidates) or 1))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        efuts = [
            ex.submit(extract.extract_candidate, c, base_no + i + 1, settings)
            for i, c in enumerate(candidates)
        ]
        for i, ef in enumerate(efuts):
            try:
                profiles.append(ef.result())
            # Boundary, as above: route the failure to review and keep the batch.
            except Exception as e:  # noqa: BLE001
                _log("extract_error", session, error_type=type(e).__name__, stage="extracting")
                profiles.append(extract.error_profile(candidates[i], base_no + i + 1))
            session.progress["done"] = len(profiles)
    return documents, profiles


def _clear_dupes(profiles: list[CandidateProfile]) -> None:
    """Reset duplicate/conflict flags before a full re-dedup (used on merge)."""
    for p in profiles:
        rs = p["extraction"]["reasons"]
        changed = False
        if p.get("duplicate_of"):
            p["duplicate_of"] = None
            if "DUPLICATE" in rs:
                rs.remove("DUPLICATE")
            changed = True
        if p.get("conflict_with"):
            p.pop("conflict_with", None)
            if "CONFLICTING_VERSIONS" in rs:
                rs.remove("CONFLICTING_VERSIONS")
            changed = True
        p.pop("verification_notes", None)  # dedup-derived; recomputed on re-dedup
        if changed and p["extraction"]["status"] == "NEEDS_REVIEW" and not rs:
            p["extraction"]["status"] = "OK"


class AnalysisService:
    """Session lifecycle + background analysis over one :class:`SessionStore`."""

    def __init__(self, store: SessionStore, workers: int = 2) -> None:
        self.store = store
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cv-analysis")

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _session(self, chat_id: str) -> Session:
        s = self.store.get(chat_id)
        if s is None:
            raise SessionNotFound()
        return s

    # ---------------------------------------------------------------- commands
    def start_analysis(
        self,
        chat_id: str,
        uploads: list[StoredUpload],
        tmp: str,
        *,
        mode: str,
        signature: str,
        settings: Settings,
    ) -> dict[str, Any]:
        """Queue analysis of saved uploads and return the 202 payload at once
        (``mode`` is described on the analyze endpoint). The worker owns, and
        removes, ``tmp``."""
        # Some chat UIs re-send every file attached earlier in the chat on each turn, so
        # ingest ONLY files this session has not already processed (by content hash).
        existing = self.store.get(chat_id)
        processed = existing.processed_hashes if existing is not None else set()
        new_raw = [u for u in uploads if u.sha256 not in processed]
        want_append = (
            (mode or "replace").lower() == "append"
            and existing is not None
            and existing.status == "ready"
            and bool(existing.profiles)
        )
        status_url = f"/sessions/{chat_id}/status"

        if want_append:
            assert existing is not None  # guaranteed by the want_append condition
            if not new_raw:
                # only already-known files were re-sent; nothing new to add
                existing.client_signature = signature
                shutil.rmtree(tmp, ignore_errors=True)
                return {
                    "chat_id": chat_id,
                    "job_id": existing.job_id,
                    "status": "ready",
                    "mode": "append",
                    "message": "no new files to add; existing batch unchanged",
                    "status_url": status_url,
                }
            base_no = max((p["display_no"] for p in existing.profiles), default=0)
            merged_hashes = set(processed) | {u.sha256 for u in new_raw}
            existing.status = "running"
            existing.error = None
            existing.client_signature = signature
            existing.progress = {"done": 0, "total": len(new_raw), "stage": "queued"}
            self._executor.submit(
                self._run_append,
                existing,
                new_raw,
                tmp,
                settings,
                base_no,
                merged_hashes,
            )
            return {
                "chat_id": chat_id,
                "job_id": existing.job_id,
                "status": "queued",
                "mode": "append",
                "message": "appending new files to existing batch; poll status",
                "status_url": status_url,
            }

        # replace / reset: if an old batch exists AND genuinely-new files were uploaded,
        # analyze ONLY the new upload (discard the old batch, e.g. "reset and analyze only
        # this file"); otherwise analyze everything provided (fresh first upload).
        to_ingest = new_raw if (existing is not None and new_raw) else uploads
        session = self.store.create(chat_id)
        session.status = "queued"
        session.client_signature = signature
        session.progress = {"done": 0, "total": len(to_ingest), "stage": "queued"}
        self._executor.submit(
            self._run_analysis,
            session,
            to_ingest,
            tmp,
            settings,
            {u.sha256 for u in to_ingest},
        )
        return {
            "chat_id": chat_id,
            "job_id": session.job_id,
            "status": "queued",  # informational; poll /status for the live state
            "mode": "replace",
            "message": "analysis started; poll status for progress and results",
            "status_url": status_url,
        }

    def reset(self, chat_id: str) -> bool:
        return self.store.reset(chat_id)

    # ---------------------------------------------------------------- workers
    def _finalize(self, session: Session, profiles: list[CandidateProfile]) -> None:
        """Dedup across the whole (possibly merged) batch and publish it."""
        dedup.mark_duplicates(profiles)
        session.profiles = profiles
        session.roster_order = [p["candidate_id"] for p in profiles]
        # Working set = ALL candidates except exact duplicates (flagged candidates stay
        # visible/queryable; only literal duplicates are dropped to avoid double-counting).
        non_dup = [p["candidate_id"] for p in profiles if not p["duplicate_of"]]
        session.view_state["current_set"] = non_dup or [p["candidate_id"] for p in profiles]
        session.summary = batch_summary(profiles)
        session.progress["stage"] = "ready"
        session.status = "ready"
        self.store.persist(session)  # survive a service restart mid-conversation

    def _run_analysis(
        self,
        session: Session,
        uploads: list[StoredUpload],
        tmp: str,
        settings: Settings,
        hashes: set[str],
    ) -> None:
        """Background worker: analyse ``uploads`` as a fresh batch, updating
        progress, then publish it."""
        t0 = time.monotonic()
        _log("analyze_start", session, file_count=len(uploads), status="running", stage="reading")
        try:
            session.status = "running"
            result = _process_documents(session, uploads, tmp, settings, base_no=0)
            if result is None:
                session.status = "error"
                session.error = _NO_SUPPORTED_FILES
                _log("analyze_done", session, t0, status="error", stage="ingest")
                return
            documents, profiles = result
            session.documents = documents
            self._finalize(session, profiles)
            session.processed_hashes = hashes
            _log(
                "analyze_done",
                session,
                t0,
                file_count=len(profiles),
                status="ready",
                stage="ready",
            )
        except ingest.ArchiveRejected as e:  # an input problem, reported verbatim
            session.status = "error"
            session.error = f"upload rejected: {e}"
            _log(
                "analyze_error",
                session,
                t0,
                status="error",
                error_type=type(e).__name__,
                stage="ingest",
            )
        # Boundary: this runs in an executor thread, where an escaping exception
        # would vanish into an unread Future and leave the session "running"
        # forever. Report it on the session and in the (PII-free) log instead.
        except Exception as e:  # noqa: BLE001
            session.status = "error"
            session.error = f"analysis failed: {e}"
            _log(
                "analyze_error",
                session,
                t0,
                status="error",
                error_type=type(e).__name__,
                stage="analysis",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _run_append(
        self,
        session: Session,
        uploads: list[StoredUpload],
        tmp: str,
        settings: Settings,
        base_no: int,
        hashes: set[str],
    ) -> None:
        """Background worker: analyse the new upload and merge it into the existing
        batch, continuing candidate numbering from ``base_no`` and re-running
        duplicate detection over the merged set. The previous batch is kept if
        the append fails."""
        old_profiles = list(session.profiles)
        try:
            session.status = "running"
            result = _process_documents(session, uploads, tmp, settings, base_no=base_no)
            if result is None:
                session.profiles = old_profiles
                session.error = "no supported CV files found in the new upload; batch unchanged"
                session.progress = {"done": 0, "total": 0, "stage": "ready"}
                session.status = "ready"
                return
            documents, new_profiles = result
            session.documents = (session.documents or []) + documents
            merged = old_profiles + new_profiles
            _clear_dupes(merged)  # recompute duplicates across the FULL merged batch
            self._finalize(session, merged)
            session.last_added = len(new_profiles)
            session.processed_hashes = hashes
        except ingest.ArchiveRejected as e:
            self._restore(session, old_profiles, f"append rejected (batch left unchanged): {e}")
        # Boundary (executor thread, see _run_analysis): keep the old batch intact.
        except Exception as e:  # noqa: BLE001
            _log(
                "append_error",
                session,
                status="ready",
                error_type=type(e).__name__,
                stage="analysis",
            )
            self._restore(session, old_profiles, f"append failed (batch left unchanged): {e}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def _restore(session: Session, old_profiles: list[CandidateProfile], error: str) -> None:
        session.profiles = old_profiles
        session.roster_order = [p["candidate_id"] for p in old_profiles]
        session.summary = batch_summary(old_profiles)
        session.progress = {"done": len(old_profiles), "total": len(old_profiles), "stage": "ready"}
        session.status = "ready"
        session.error = error

    # ---------------------------------------------------------------- queries
    def active_sessions(self) -> int:
        return self.store.count()

    def status(self, chat_id: str, settings: Settings) -> dict[str, Any]:
        s = self._session(chat_id)
        out = s.public_status()
        if s.status == "ready":
            out["summary"] = s.summary
            rows = roster(s.profiles)
            out["roster"] = rows
            out["roster_markdown"] = render.roster_markdown(s.summary, rows)
        return with_mode(out, settings)

    def facts(self, chat_id: str, settings: Settings) -> dict[str, Any]:
        """Grounded candidate facts for a client model, plus the last score's
        appendix; the bare status while the batch is not ready."""
        s = self._session(chat_id)
        if s.status != "ready":
            return {"status": s.status, "progress": s.progress, "error": s.error}
        facts = render.facts_block(s.profiles, s.summary)
        last = (s.view_state or {}).get("last_score") or {}
        if last.get("appendix"):
            # keep follow-up answers consistent with the deterministic engine's scores
            facts = facts + "\n\n" + last["appendix"]
        return with_mode(
            {
                "status": "ready",
                "count": len(s.profiles),
                "summary": s.summary,
                "roster_markdown": render.roster_markdown(s.summary, roster(s.profiles)),
                "facts": facts,
            },
            settings,
        )

    def audit(self, chat_id: str, request: AuditRequest) -> dict[str, Any]:
        """Protected-attribute leakage report, always (proves the group-blind
        invariant held); plus the four-fifths adverse-impact analysis when the
        caller supplies group labels and the selection outcome."""
        s = self._session(chat_id)
        texts = []
        last = (s.view_state or {}).get("last_score") or {}
        if last.get("appendix"):
            texts.append(last["appendix"])
        report: dict[str, Any] = {
            "chat_id": s.chat_id,
            "leakage": fairness.leakage_report(s.profiles, texts),
        }
        if request.labels is not None and request.selected is not None:
            report["adverse_impact"] = fairness.four_fifths_audit(request.selected, request.labels)
        return report

    def query(self, chat_id: str, question: str, settings: Settings) -> dict[str, Any]:
        """Answer a follow-up question over the analysed batch."""
        s = self._session(chat_id)
        if s.status != "ready":
            return {
                "chat_id": s.chat_id,
                "status": s.status,
                "answer": f"The batch is still **{s.status}** "
                f"({s.progress.get('done')}/{s.progress.get('total')}). Ask again shortly.",
            }
        return {"chat_id": s.chat_id, "status": s.status, **engine.answer(s, question, settings)}

    def score(self, chat_id: str, request: ScoreRequest, settings: Settings) -> dict[str, Any]:
        """Deterministic fit scoring of the chat's analyzed batch against a job
        description; ``scorable: false`` (with the reason) when it cannot be."""
        s = self._session(chat_id)
        if s.status != "ready":
            return {
                "chat_id": s.chat_id,
                "status": s.status,
                "scorable": False,
                "reason": f"batch still {s.status} "
                f"({s.progress.get('done')}/{s.progress.get('total')})",
            }
        t0 = time.monotonic()
        try:
            result = scoring_service.score_session(
                s, request.jd_text, settings, lang=(request.lang or "en"), top_n=request.top_n
            )
        except scoring_rubric.RubricError as e:
            _log("score_error", s, t0, status="error", error_type=type(e).__name__, stage="rubric")
            return {"chat_id": s.chat_id, "status": "ready", "scorable": False, "reason": str(e)}
        self.store.persist(s)
        _log("score_done", s, t0, status="ready", stage="scoring")
        return with_mode({"chat_id": s.chat_id, "status": "ready", **result}, settings)
