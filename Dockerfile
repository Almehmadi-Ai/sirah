# Sirah: privacy-first CV screening service image.
#
# Base pinned to an exact patch release AND its multi-arch index digest, so a
# rebuild is reproducible and a re-pushed tag cannot change what ships.
# Dependabot (docker ecosystem) proposes digest/tag updates.
FROM python:3.15.0-slim-bookworm@sha256:122756f3fe72c4f5d3d71897888204ee85774bc79de952555b8a474e1d0a5d53

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# OS + OCR packages. Poppler intentionally omitted (pypdfium2 renders pages).
# Debian package versions are not pinned: superseded versions disappear from
# the mirrors (breaking rebuilds); the digest-pinned base fixes the release.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-ara \
        tesseract-ocr-eng \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged runtime user with fixed ids (predictable ownership for a mounted
# volume; numeric USER below lets Kubernetes verify runAsNonRoot). /app/data is
# the mount point for the OPT-IN session persistence (CV_DATA_DIR=/app/data) and
# the only path that user can write; storage is memory-only unless it is set.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app/data \
    && chown app:app /app/data

WORKDIR /app

# Dependencies first, from the fully-pinned lock (the same set the Security
# workflow audits), so this layer is cached until the lock changes.
COPY requirements-lock.txt .
RUN pip install -r requirements-lock.txt

# Application code only - no tests, docs or scripts (see .dockerignore). Owned
# by root and read-only for the runtime user.
COPY LICENSE NOTICE ./
COPY app/ ./app/

USER 10001:10001

EXPOSE 8089
HEALTHCHECK --interval=30s --timeout=5s --retries=5 --start-period=20s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8089/health', timeout=4)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8089", "--workers", "1"]
