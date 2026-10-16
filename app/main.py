"""Sirah, privacy-first CV screening — FastAPI service (the HTTP layer).

Endpoints:
  GET  /health                       (open)
  POST /sessions/{chat_id}/analyze   (auth, multipart)  -> ingest a batch ONCE
  GET  /sessions/{chat_id}/status    (auth)
  GET  /sessions/{chat_id}/facts     (auth)              -> grounded facts for a client LLM
  POST /sessions/{chat_id}/query     (auth)              -> multi-turn Q&A
  POST /sessions/{chat_id}/score     (auth)              -> fit report against a JD
  POST /sessions/{chat_id}/audit     (auth)              -> leakage + adverse-impact report
  POST /sessions/{chat_id}/reset     (auth)              -> drop the session (privacy)

This module only authenticates, validates input and maps outcomes to HTTP; the
orchestration lives in ``app/service.py``. Analysis gaps are returned as 200
results, not HTTP errors, so the calling client's error handling never masks a
safe "needs review" outcome. Only infra / input failures use 4xx/5xx.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import os
import re
import shutil
import tempfile
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse

from . import __version__
from .config import (
    Settings,
    enforce_auth_configured,
    enforce_local_endpoints,
    get_settings,
    remote_endpoints,
)
from .extraction.ocr import tesseract_available, tesseract_langs
from .models import AuditRequest, HealthResponse, QueryRequest, ScoreRequest, parse_options
from .pipeline import ingest
from .pipeline.ingest import StoredUpload
from .service import AnalysisService, SessionNotFound
from .session_store import SessionStore

router = APIRouter()
_LAZY_LOCK = threading.Lock()
_UPLOAD_CHUNK = 1024 * 1024


def _build_service(settings: Settings) -> AnalysisService:
    # The store is created HERE - at startup, never at import - and writes to
    # disk only when CV_DATA_DIR is set (memory-only by default).
    return AnalysisService(
        SessionStore(ttl_minutes=settings.session_ttl_minutes, data_dir=settings.data_dir)
    )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Startup guards: refuse to run without authentication, or if any endpoint
    # that receives candidate data is off-box, unless the operator explicitly
    # (and loudly) opted out. Only then is the session store created.
    s = get_settings()
    enforce_auth_configured(s)
    enforce_local_endpoints(s)
    service = _build_service(s)
    app.state.service = service
    try:
        yield
    finally:
        app.state.service = None
        service.shutdown()


def get_service(request: Request) -> AnalysisService:
    """The app's AnalysisService. Normally created by the lifespan; built lazily
    on first use if the host runs the app without lifespan events."""
    service = getattr(request.app.state, "service", None)
    if service is None:
        with _LAZY_LOCK:
            service = getattr(request.app.state, "service", None)
            if service is None:
                service = _build_service(get_settings())
                request.app.state.service = service
    return service


async def _session_not_found(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


def require_key(x_api_key: str | None = Header(None)) -> None:
    """Fail closed: with no key configured, nothing is served unless the
    development-only CV_DISABLE_AUTH opt-out is set (enforced at startup too)."""
    s = get_settings()
    if not s.api_key:
        if s.auth_disabled:
            return
        raise HTTPException(503, "authentication is not configured on the server (CV_API_KEY)")
    if x_api_key is None or not hmac.compare_digest(
        x_api_key.encode("utf-8"), s.api_key.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


def _safe_name(name: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(name or "file"))


def _safe_chat_id(chat_id: str) -> str:
    cid = re.sub(r"[^A-Za-z0-9._:-]", "_", chat_id or "")
    if not cid:
        raise HTTPException(status_code=400, detail="missing chat_id")
    return cid[:128]


async def _receive_uploads(
    files: list[UploadFile], settings: Settings
) -> tuple[str, list[StoredUpload]]:
    """Stream the request's files to a fresh temp dir, enforcing the per-file
    size cap as the bytes arrive (an oversized upload is never held in memory)
    and the archive limits. Returns (temp_dir, uploads); the caller hands the dir
    to the worker. Raises HTTPException (413 / 415) after cleaning up."""
    limit = settings.max_upload_mb * 1024 * 1024
    tmp = tempfile.mkdtemp(prefix="cv_")
    uploads: list[StoredUpload] = []
    try:
        for i, uf in enumerate(files):
            name = uf.filename or "file"
            # Keyed by position, never by name: two different CVs may both be
            # called resume.pdf, and must not overwrite each other.
            path = os.path.join(tmp, f"{i:04d}_{_safe_name(name)}")
            digest, size = hashlib.sha256(), 0
            with open(path, "wb") as f:
                while chunk := await uf.read(_UPLOAD_CHUNK):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, f"file too large: {name}")
                    digest.update(chunk)
                    f.write(chunk)
            if path.lower().endswith((".zip", ".docx")):
                try:  # zip-bomb defence: central directory checked before any extraction
                    ingest.check_archive(path, os.path.basename(name), settings)
                except ingest.ArchiveRejected as e:
                    raise HTTPException(413, f"upload rejected: {e}") from e
            uploads.append(StoredUpload(name, path, digest.hexdigest()))
        if not uploads:
            raise HTTPException(415, "no files uploaded")
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return tmp, uploads


@router.get("/health", response_model=HealthResponse)
def health(service: AnalysisService = Depends(get_service)) -> dict[str, Any]:
    s = get_settings()
    return {
        "status": "ok",
        "service": "sirah",
        "version": __version__,
        "llm_mode": s.llm_mode,
        "ocr": {
            "tesseract": tesseract_available(),
            "langs": tesseract_langs(),
            "mode": s.ocr_mode,
            # local VLM rescue of unreadable pages (never runs in mock mode)
            "vision_enabled": s.vlm_enabled and s.llm_mode != "mock",
        },
        "models": {"llm": s.llm_model, "embed": s.embed_model, "ollama": s.ollama_url},
        "privacy": {
            "endpoints_local": not remote_endpoints(s),
            "remote_endpoints_allowed": s.allow_remote_endpoints,
        },
        "sessions": {"active": service.active_sessions(), "ttl_minutes": s.session_ttl_minutes},
        "time": _dt.datetime.now(_dt.UTC).isoformat(),
    }


@router.post("/sessions/{chat_id}/analyze", status_code=202)
async def analyze_endpoint(
    chat_id: str,
    files: list[UploadFile] = File(...),
    options: str = Form("{}"),
    signature: str = Form(""),
    mode: str = Form("replace"),
    _=Depends(require_key),
    service: AnalysisService = Depends(get_service),
) -> JSONResponse:
    """Start analysis of an uploaded batch (files and/or ZIP) for this chat and
    return IMMEDIATELY with a job_id. Poll GET /sessions/{chat_id}/status for live
    progress; when status is 'ready' the status response includes the roster +
    summary. Analysis runs in a background worker so a 25-200 CV batch never blocks
    the request.

    mode='replace' (default): analyze this upload as a fresh batch (previous batch
    for this chat is discarded). mode='append': MERGE this upload into the existing
    ready batch for this chat -- candidate numbering continues (e.g. 60 -> 61..80)
    and duplicate detection re-runs across the full merged batch. append falls back
    to a fresh analysis when there is no ready batch yet."""
    s = get_settings()
    cid = _safe_chat_id(chat_id)
    _ = parse_options(options)  # reserved; tolerated but not yet interpreted
    tmp, uploads = await _receive_uploads(files, s)
    accepted = service.start_analysis(cid, uploads, tmp, mode=mode, signature=signature, settings=s)
    return JSONResponse(status_code=202, content=accepted)


@router.get("/sessions/{chat_id}/status")
def status_endpoint(
    chat_id: str, _=Depends(require_key), service: AnalysisService = Depends(get_service)
) -> dict[str, Any]:
    return service.status(_safe_chat_id(chat_id), get_settings())


@router.get("/sessions/{chat_id}/facts")
def facts_endpoint(
    chat_id: str, _=Depends(require_key), service: AnalysisService = Depends(get_service)
) -> dict[str, Any]:
    """Grounded candidate facts for the model to reason over (the recommended path:
    the model ranks/filters/compares using ONLY these facts). Returns 200 with a
    status when not yet ready so the caller can show progress."""
    return service.facts(_safe_chat_id(chat_id), get_settings())


@router.post("/sessions/{chat_id}/audit")
def audit_endpoint(
    chat_id: str,
    body: AuditRequest | None = None,
    _=Depends(require_key),
    service: AnalysisService = Depends(get_service),
) -> dict[str, Any]:
    """Fairness reporting for the chat's batch. ALWAYS returns the
    protected-attribute leakage report (proves the group-blind invariant held).
    If the caller supplies demographic ``labels`` (candidate_id -> {axis: group})
    and a ``selected`` map (candidate_id -> bool), it also returns four-fifths-rule
    adverse-impact ratios - figures that support a bias audit, not an audit."""
    return service.audit(_safe_chat_id(chat_id), body or AuditRequest())


@router.post("/sessions/{chat_id}/query")
def query_endpoint(
    chat_id: str,
    body: QueryRequest,
    _=Depends(require_key),
    service: AnalysisService = Depends(get_service),
) -> dict[str, Any]:
    """Multi-turn conversational query over the already-analyzed batch. The chat's
    batch is resolved by chat_id — no re-upload, no re-processing."""
    return service.query(_safe_chat_id(chat_id), body.question, get_settings())


@router.post("/sessions/{chat_id}/score")
def score_endpoint(
    chat_id: str,
    body: ScoreRequest,
    _=Depends(require_key),
    service: AnalysisService = Depends(get_service),
) -> dict[str, Any]:
    """Deterministic fit scoring of the chat's analyzed batch against a job
    description. The rubric is derived from THIS JD at runtime (any role /
    industry / language); matching is evidence-based; aggregation, ranking and
    the rendered report are fully deterministic. Same session + same JD =>
    byte-identical report. Returns 200 with scorable=false (and an explanatory
    report) when the JD lacks concrete requirements."""
    return service.score(_safe_chat_id(chat_id), body, get_settings())


@router.post("/sessions/{chat_id}/reset")
def reset_endpoint(
    chat_id: str, _=Depends(require_key), service: AnalysisService = Depends(get_service)
) -> dict[str, bool]:
    return {"reset": service.reset(_safe_chat_id(chat_id))}


def create_app() -> FastAPI:
    """Build the ASGI application. Side-effect free: nothing is created or read
    from disk until the app starts (see ``_lifespan``)."""
    application = FastAPI(title="Sirah", version=__version__, lifespan=_lifespan)
    application.include_router(router)
    application.add_exception_handler(SessionNotFound, _session_not_found)
    return application


app = create_app()
