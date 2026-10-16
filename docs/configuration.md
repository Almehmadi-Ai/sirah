# Configuration

Every setting is a `CV_*` environment variable, read when it is used (so tests can
change one per case). Most are parsed in `app/config.py`; the two scoring weights
are read by `app/scoring/engine.py`. `.env.example` and `docker-compose.yml` show
the values the container uses.

## Service and privacy

| Variable | Default | What it does |
|---|---|---|
| `CV_API_KEY` | — (**required**) | Key clients send as `X-API-Key`. The service refuses to start without it. |
| `CV_DISABLE_AUTH` | `false` | Development only: run without a key (logged as a warning; ignored when a key is set). |
| `CV_LLM_MODE` | `mock` | `mock` (deterministic, no models) or `ollama` (the local models below). |
| `CV_OLLAMA_URL` | `http://host.docker.internal:11434` | Local Ollama. It receives CV text and page images, so it **must be local** — loopback, a private-network address, `localhost`, `host.docker.internal` or a single-label service name — or the service refuses to start. |
| `CV_ALLOW_REMOTE_ENDPOINTS` | `false` | Deliberately allow a non-local `CV_OLLAMA_URL` (logged as a privacy warning on every start). |
| `CV_DATA_DIR` | empty (memory only) | Opt-in persistence of ready sessions and the JD-rubric cache; same TTL, deleted on expiry or reset. |
| `CV_SESSION_TTL_MINUTES` | `120` | Sliding session lifetime. |

## Models (`ollama` mode)

| Variable | Default | What it does |
|---|---|---|
| `CV_LLM_MODEL` / `CV_EMBED_MODEL` | `qwen3.6:35b` / `bge-m3:latest` | Reasoning LLM and multilingual embedder. |
| `CV_VLM_ENABLED` / `CV_VLM_MODEL` | `true` / `qwen2.5vl:7b` | Local vision model for pages OCR cannot read. |
| `CV_VLM_TIMEOUT` / `CV_VLM_MAX_PAGES_PER_DOC` | `120` / `6` | Seconds per page; pages per document. |
| `CV_MATCHER` | `judge` | `judge` (grounded LLM judge) or `legacy` (keyword + cosine); a rollback that needs no rebuild. |
| `CV_SCORE_TIMEOUT` | `240` | Seconds allowed for scoring calls. |

## Scoring

| Variable | Default | What it does |
|---|---|---|
| `CV_W_REQUIRED` / `CV_W_PREFERRED` | `1.0` / `0.4` | Weights of required vs preferred requirements. |

## OCR

| Variable | Default | What it does |
|---|---|---|
| `CV_OCR_MODE` | `auto` | `off`, `auto` (only thin/scanned pages) or `force`. |
| `CV_OCR_LANGS` / `CV_OCR_DPI` | `ara+eng` / `300` | Tesseract languages and render resolution. |
| `CV_OCR_MIN_CONFIDENCE` / `CV_OCR_HIGH_CONFIDENCE` | `60` / `80` | Below the minimum a CV is flagged for review; at or above the high mark OCR is treated as reliable. |
| `CV_OCR_TEXT_MIN` | `20` | Characters of text layer that count as "present" on a page. |

## Limits and concurrency

| Variable | Default | What it does |
|---|---|---|
| `CV_MAX_FILES` / `CV_MAX_UPLOAD_MB` / `CV_MAX_PAGES_PER_DOC` | `300` / `50` / `30` | Batch, per-file (uploaded or inside a ZIP) and per-document caps. |
| `CV_MAX_ZIP_ENTRIES` / `CV_MAX_ZIP_TOTAL_MB` / `CV_MAX_ZIP_RATIO` | `1000` / `250` / `100` | Zip-bomb limits, checked before extraction and while streaming. |
| `CV_EXTRACT_WORKERS` / `CV_ACQUIRE_WORKERS` | `4` / `4` | Concurrent extraction (LLM) and acquisition (OCR) workers. |
