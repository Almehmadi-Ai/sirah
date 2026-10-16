"""Runtime configuration, sourced entirely from environment variables so the
package is portable (no machine-specific values baked in). Read at call time so
the service and tests always see current env.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import urllib.parse
from dataclasses import dataclass

from . import __version__

logger = logging.getLogger("sirah")


@dataclass(frozen=True)
class Settings:
    # --- auth / service ---
    api_key: str  # required: the service refuses to start without one...
    auth_disabled: bool  # ...unless this DEV-ONLY opt-out is set (and no key is)

    # --- OCR (local Tesseract; unreadable pages escalate to the local VLM) ---
    ocr_mode: str  # off | auto | force
    ocr_min_confidence: float  # below this a page is flagged LOW_OCR_CONFIDENCE
    ocr_high_confidence: float  # at/above this OCR is treated as reliable
    ocr_dpi: int
    ocr_langs: str
    ocr_text_min: int  # min chars for a page's text layer to be "sufficient"

    # --- ingest caps ---
    max_files: int
    max_pages_per_doc: int
    max_upload_mb: int  # per file, uploaded directly or decompressed from a ZIP
    max_zip_entries: int  # central-directory entries per archive (zip-bomb defence)
    max_zip_total_mb: int  # total decompressed size per archive
    max_zip_ratio: int  # max compression ratio of any sizeable archive member

    # --- VLM escalation (LOCAL vision model for unreadable pages) ---
    vlm_enabled: bool
    vlm_model: str  # e.g. qwen2.5vl:7b (must be on the local Ollama)
    vlm_timeout: int  # seconds per page transcription
    vlm_max_pages_per_doc: int  # latency cap per document

    # --- session (ephemeral, per chat_id) ---
    session_ttl_minutes: int
    extract_workers: int  # concurrent LLM extraction calls (semaphore)
    acquire_workers: int  # concurrent CPU acquire (OCR/layout) workers
    data_dir: str  # opt-in persistence dir; '' (the default) = memory only, nothing on disk

    # --- local model backends (Ollama, on-box) ---
    ollama_url: str  # receives CV text and page images: MUST be local (see guard below)
    allow_remote_endpoints: bool  # explicit, loudly-logged opt-out of the locality guard
    llm_model: str
    embed_model: str
    llm_mode: str  # 'mock' (default, zero-dependency) | 'ollama'

    # --- scoring matcher: 'legacy' (keyword+cosine) | 'judge' (grounded LLM reasoning) ---
    matcher: str
    score_timeout: int

    version: str


def get_settings() -> Settings:
    g = os.environ.get
    return Settings(
        api_key=g("CV_API_KEY", "").strip(),
        auth_disabled=g("CV_DISABLE_AUTH", "false").strip().lower() == "true",
        ocr_mode=g("CV_OCR_MODE", "auto"),
        ocr_min_confidence=float(g("CV_OCR_MIN_CONFIDENCE", "60")),
        ocr_high_confidence=float(g("CV_OCR_HIGH_CONFIDENCE", "80")),
        ocr_dpi=int(g("CV_OCR_DPI", "300")),
        ocr_langs=g("CV_OCR_LANGS", "ara+eng"),
        ocr_text_min=int(g("CV_OCR_TEXT_MIN", "20")),
        max_files=int(g("CV_MAX_FILES", "300")),
        max_pages_per_doc=int(g("CV_MAX_PAGES_PER_DOC", "30")),
        max_upload_mb=int(g("CV_MAX_UPLOAD_MB", "50")),
        max_zip_entries=int(g("CV_MAX_ZIP_ENTRIES", "1000")),
        max_zip_total_mb=int(g("CV_MAX_ZIP_TOTAL_MB", "250")),
        max_zip_ratio=int(g("CV_MAX_ZIP_RATIO", "100")),
        vlm_enabled=g("CV_VLM_ENABLED", "true").lower() == "true",
        vlm_model=g("CV_VLM_MODEL", "qwen2.5vl:7b"),
        vlm_timeout=int(g("CV_VLM_TIMEOUT", "120")),
        vlm_max_pages_per_doc=int(g("CV_VLM_MAX_PAGES_PER_DOC", "6")),
        session_ttl_minutes=int(g("CV_SESSION_TTL_MINUTES", "120")),
        extract_workers=int(g("CV_EXTRACT_WORKERS", "4")),
        acquire_workers=int(g("CV_ACQUIRE_WORKERS", "4")),
        data_dir=g("CV_DATA_DIR", "").strip(),
        ollama_url=g("CV_OLLAMA_URL", "http://host.docker.internal:11434"),
        allow_remote_endpoints=g("CV_ALLOW_REMOTE_ENDPOINTS", "false").strip().lower() == "true",
        llm_model=g("CV_LLM_MODEL", "qwen3.6:35b"),
        embed_model=g("CV_EMBED_MODEL", "bge-m3:latest"),
        # 'mock' = deterministic, no-model backend (the PUBLIC DEFAULT, so the
        # service runs with zero dependencies: no Ollama, no model, no network).
        # 'ollama' = call the local models configured above.
        llm_mode=g("CV_LLM_MODE", "mock").strip().lower(),
        # DEFAULT = judge (grounded LLM reasoning). Instant rollback with NO
        # rebuild: set env CV_MATCHER=legacy to fall back to the keyword+cosine
        # matcher, which stays fully intact.
        matcher=g("CV_MATCHER", "judge").strip().lower(),
        score_timeout=int(g("CV_SCORE_TIMEOUT", "240")),
        version=__version__,
    )


# ---------------------------------------------------------------------------
# Privacy guard: candidate data must not leave the operator's machine/network.
# ---------------------------------------------------------------------------

# EVERY setting naming an endpoint that candidate data is sent to, mapped to its
# environment variable. The startup guard checks all of them, and a test fails
# if any ``*_url`` setting (or any ``settings.*_url`` use in app code) is missing
# here, so a new endpoint cannot ship unguarded.
OUTBOUND_ENDPOINTS: dict[str, str] = {
    "ollama_url": "CV_OLLAMA_URL",  # LLM prose/judge/JD calls, VLM page images, embeddings
}

# Hostnames that always mean "this machine". host.docker.internal is how the
# Compose service reaches the host's Ollama (docker-compose.yml maps it to the
# host gateway on Linux).
_LOCAL_HOSTNAMES = frozenset({"localhost", "host.docker.internal"})

# Loopback plus private address space. Private ranges are required, not a
# convenience: a container reaches an on-box model through the Docker bridge
# (e.g. 172.17.0.1). Link-local (169.254/16 - cloud metadata) and every public
# address are refused.
_LOCAL_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "::1/128",
        "fc00::/7",
    )
)

# A single-label name such as a Compose service ('ollama') resolves only inside
# the local network. Must start with a letter, so numeric IPv4 shorthands that a
# resolver would expand ('134744072' = 8.8.8.8, '0x7f000001') never qualify.
_SERVICE_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


def is_local_endpoint(url: str) -> bool:
    """True only if ``url`` is an http(s) URL whose HOST is this machine or the
    operator's private network.

    Parsed, never substring-matched: the host comes from ``urllib.parse`` (so
    ``http://localhost@evil.com`` is evil.com) and must be an exact local name,
    a single-label service name, or an IP literal in loopback/private space - so
    ``localhost.attacker.net`` and ``127.0.0.1.nip.io`` are remote."""
    try:
        parts = urllib.parse.urlsplit((url or "").strip())
        host = parts.hostname  # lower-cased, brackets and userinfo removed
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not host:
        return False
    host = host.rstrip(".")  # 'localhost.' is the same host
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])  # drop an IPv6 zone id
    except ValueError:
        return host in _LOCAL_HOSTNAMES or bool(_SERVICE_NAME.match(host))
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in net for net in _LOCAL_NETWORKS)


def remote_endpoints(settings: Settings) -> list[tuple[str, str]]:
    """[(env var, host)] for every configured outbound endpoint that is NOT local."""
    out: list[tuple[str, str]] = []
    for attr, env in OUTBOUND_ENDPOINTS.items():
        url = getattr(settings, attr)
        if url and not is_local_endpoint(url):
            try:
                host = urllib.parse.urlsplit(url.strip()).hostname or "?"
            except ValueError:
                host = "?"
            out.append((env, host))
    return out


class InsecureConfigError(RuntimeError):
    """Raised at startup when the service would run without authentication."""


def enforce_auth_configured(settings: Settings) -> None:
    """Startup guard: authentication fails CLOSED. Without CV_API_KEY the service
    refuses to start, unless CV_DISABLE_AUTH=true is set for local development -
    which is logged as a warning on every start. A configured key always wins."""
    if settings.api_key:
        if settings.auth_disabled:
            logger.warning("CV_DISABLE_AUTH is ignored because CV_API_KEY is set.")
        return
    if not settings.auth_disabled:
        raise InsecureConfigError(
            "Refusing to start: CV_API_KEY is not set, so candidate data would be "
            "served without authentication. Set CV_API_KEY (clients send it as the "
            "X-API-Key header), or set CV_DISABLE_AUTH=true for local development only."
        )
    logger.warning(
        "SECURITY WARNING: authentication is DISABLED (CV_DISABLE_AUTH=true, no "
        "CV_API_KEY). Every request is accepted - development use only."
    )


class RemoteEndpointError(RuntimeError):
    """Raised at startup when candidate data would be sent off the operator's network."""


def enforce_local_endpoints(settings: Settings) -> None:
    """Startup guard. Refuse to run if any outbound endpoint is remote, unless the
    operator explicitly opted out with CV_ALLOW_REMOTE_ENDPOINTS=true - in which
    case every remote endpoint is logged as a privacy warning on each start."""
    remote = remote_endpoints(settings)
    if remote and not settings.allow_remote_endpoints:
        names = ", ".join(f"{env} (host {host})" for env, host in remote)
        raise RemoteEndpointError(
            "Refusing to start: candidate CV text and page images would be sent to "
            f"a non-local endpoint: {names}. Point it at this machine or your private "
            "network, or set CV_ALLOW_REMOTE_ENDPOINTS=true to send candidate data "
            "off-box deliberately."
        )
    for env, host in remote:
        logger.warning(
            "PRIVACY WARNING: %s points at %s, which is NOT local. Candidate CV text "
            "and page images will leave this machine (CV_ALLOW_REMOTE_ENDPOINTS=true).",
            env,
            host,
        )
    for legacy in ("CV_VISION_OCR_URL", "CV_ALLOW_CLOUD_OCR"):
        if os.environ.get(legacy, "").strip():
            logger.warning("%s is no longer used and is ignored; remove it.", legacy)
