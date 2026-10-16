# Architecture

This document describes the system's planes, the responsibility of each module,
and — most importantly — the **trust boundaries** that separate what the model
produces from what the code guarantees.

The organising principle throughout is:

> **The LLM points; the code reads.** The model is used only for language
> understanding. Anything that must be *true* is computed deterministically and
> can overrule the model.

---

## The four planes

```mermaid
%%{init: {'theme':'base','themeVariables':{'fontFamily':'ui-monospace, Menlo, Consolas, monospace','primaryColor':'#151517','primaryBorderColor':'#2C2C31','primaryTextColor':'#E6E6E9','lineColor':'#57575E','clusterBkg':'#0C0C0E','clusterBorder':'#242429','titleColor':'#9A9AA2'}}}%%
flowchart LR
    subgraph I["1 · Interface plane"]
        M["main.py · service.py<br/>endpoints · auth · async worker<br/>session store · mode labelling"]
    end
    subgraph E["2 · Extraction plane"]
        EP["pipeline/ + extraction/<br/>document to grounded profile"]
    end
    subgraph SC["3 · Scoring plane"]
        SP["scoring/ + query/<br/>profiles + JD to report / answers"]
    end
    subgraph G["4 · Guardrail plane (cross-cutting)"]
        GP["injection · protected-attrs<br/>evidence grounding · fairness · safe logging"]
    end
    B["Model backend<br/>mock  |  ollama"]

    I --> E --> SC --> I
    G -. wraps .-> E
    G -. wraps .-> SC
    E --> B
    SC --> B
```

### 1. Interface plane — `app/main.py`, `app/service.py`, `app/config.py`, `app/session_store.py`

The only plane that speaks HTTP. It owns:

- **Endpoints & auth** (`main.py`). Eight thin routes (see [api.md](api.md)) that
  authenticate, validate input (pydantic request models) and map outcomes to HTTP;
  every data route requires `X-API-Key`. Analysis gaps are returned as `200`
  results with a *Needs Review* reason, not HTTP errors — only genuine infra/input
  failures use 4xx/5xx, so a client's error handling never masks a safe "review
  this" outcome.
- **Orchestration** (`service.py`). The session lifecycle (fresh batch vs append,
  skipping files a chat UI re-sends), the async worker, duplicate detection over
  the merged batch, and the read models (summary, roster, facts, audit) — no HTTP
  in it, so it is testable on its own.
- **The async worker.** `analyze` returns `202` immediately and runs the batch on
  a background thread pool, so a 25–200 CV upload never blocks the request; the
  client polls `status`.
- **A typed profile** (`profiles.py`). The candidate profile is a plain,
  JSON-serialisable dict whose shape is declared as `TypedDict`s, so every
  producer and consumer is checked by mypy.
- **The ephemeral session store.** Keyed by a client-supplied `chat_id`, holding
  the analysed profiles and evolving view-state with a sliding TTL, in memory only
  by default. It is created when the app starts (in the lifespan), never at
  import. With `CV_DATA_DIR` set (opt-in), ready sessions are also persisted
  atomically so a restart doesn't lose a batch mid-conversation; expiry (and
  `reset`) erase both memory and disk.
- **Configuration.** Every knob is an environment variable (`CV_*`) read at call
  time, so the package is portable and the tests can flip behaviour per-case.

### 2. Extraction plane — `app/pipeline/`, `app/extraction/`

Turns an untrusted document into a **grounded candidate profile**. One file is
always one candidate's worth of pages — extraction never mixes candidates.

```mermaid
%%{init: {'theme':'base','themeVariables':{'fontFamily':'ui-monospace, Menlo, Consolas, monospace','primaryColor':'#151517','primaryBorderColor':'#2C2C31','primaryTextColor':'#E6E6E9','lineColor':'#57575E','clusterBkg':'#0C0C0E','clusterBorder':'#242429','titleColor':'#9A9AA2'}}}%%
flowchart TB
    U["upload (file / ZIP)"] --> ing["ingest<br/>unzip · classify (pdf/docx/image) · cap"]
    ing --> acq["acquire<br/>text layer (pdfplumber/docx)<br/>then OCR if image-only<br/>then VLM rescue if OCR fails"]
    acq --> seg["segment<br/>1 document to 1 candidate"]
    seg --> inj["injection.sanitize<br/>neutralise model-directed text FIRST"]
    inj --> anc["anchors (deterministic)<br/>email · phone · links · years"]
    anc --> llm["LLM prose read<br/>name · title · skills · education<br/>(+ verbatim quote per field)"]
    llm --> ver["quote-verification<br/>code finds each quote, attaches page<br/>unverifiable is dropped"]
    ver --> gate["coverage + gating<br/>OK | NEEDS_REVIEW (+ reasons)"]
    gate --> prof["CandidateProfile<br/>identity · experiences · skills ·<br/>education · years · evidence"]
```

Key modules:

| Module | Responsibility |
|---|---|
| `pipeline/ingest.py` | Expand ZIPs, classify each file (`pdf` / `docx` / `image`), enforce count/size/entry caps. |
| `pipeline/acquire.py` | Get text: native text layer first; **OCR** (Tesseract, ara+eng) only for image-only pages; optional **VLM** transcription for pages OCR still can't read. |
| `extraction/pdf_text.py`, `ocr.py`, `image_only.py`, `pdf_render.py`, `vlm.py` | The acquisition primitives (text, OCR, scanned-page detection, page rasterisation via **pypdfium2**, local vision transcription). |
| `pipeline/segment.py` | Map documents to candidates; flag a file that appears to hold more than one CV. |
| `pipeline/anchors.py` | **Deterministic** extraction of email/phone/links and the arithmetic of total years from dated roles (with overlap merging). No model involved. |
| `pipeline/extract.py` | Orchestrate a candidate: run the injection screen, the anchors, the LLM prose read, quote-verification, protected-attribute scrubbing, and confidence gating. |
| `pipeline/evidence.py` | Quote and term verification: find a claim in the candidate's pages and attach the page number (shared with the scoring judge and matcher). |
| `pipeline/names.py` | Name heuristics: reject headings, job titles and template placeholders offered as a name; normalise its case. |
| `pipeline/dedup.py` | Detect exact duplicates and same-contact-different-content conflicts across the batch — while keeping two different people who share a name separate. |
| `pipeline/llm.py` | The single JSON chat entry point (`chat_json`), which dispatches to the mock or Ollama backend, and `pick` for reading the model's JSON under synonym keys. |

### 3. Scoring plane — `app/scoring/`, `app/query/`

Consumes profiles (never raw documents) and produces the ranked fit report and
conversational answers.

```mermaid
%%{init: {'theme':'base','themeVariables':{'fontFamily':'ui-monospace, Menlo, Consolas, monospace','primaryColor':'#151517','primaryBorderColor':'#2C2C31','primaryTextColor':'#E6E6E9','lineColor':'#57575E','clusterBkg':'#0C0C0E','clusterBorder':'#242429','titleColor':'#9A9AA2'}}}%%
flowchart TB
    JD["job description"] --> rub["rubric.build<br/>JD to requirements<br/>(skill / years / education …)<br/>vague JD becomes NOT SCORABLE"]
    prof["candidate profiles"] --> MJ
    rub --> MJ
    subgraph MJ["per-requirement judgment (CV_MATCHER)"]
        j1["judge: grounded LLM reasoning<br/>(relevance, degree field, specific tech)"]
        j2["legacy: keyword + cosine<br/>(deterministic)"]
    end
    MJ --> eng["engine<br/>weights · hard-minimum caps ·<br/>banding · deterministic ranking"]
    eng --> rep["report<br/>matched / missing / evidence<br/>+ human-readable review lanes"]
```

| Module | Responsibility |
|---|---|
| `scoring/rubric.py` | The **only** LLM step in scoring: the JD to a structured rubric (requirements + keywords + hard minimums); deterministic post-validation cleans and bounds it. A JD with no extractable requirements is flagged *not scorable* rather than scored with fabricated precision. |
| `scoring/judge.py` | The grounded-reasoning judge: for each requirement it returns a verdict + a cited quote; code then **grounds** the quote (must occur in the CV) — an ungrounded "met" collapses to *unverified*. Arithmetic stays in code. |
| `scoring/match.py` | The legacy deterministic matcher (keyword + embedding cosine) — the mock/fallback path, selected by `CV_MATCHER=legacy` and used automatically for any candidate the judge fails on. |
| `scoring/engine.py` | Deterministic aggregation: verdict weights, hard-minimum caps, score banding, and tie-broken ranking. Pure code — reproducible. |
| `scoring/report.py` | Renders the bilingual fit report (matched/missing/evidence, review lanes) — the single source of truth for formatting. |
| `scoring/calibration.py` | Judge accuracy + Cohen's κ against a gold set, with a drift alarm. |
| `scoring/service.py` | Orchestrates the above, with per-`(JD, mode)` caching so a repeated JD returns byte-identical results instantly. |
| `query/*` | The multi-turn conversation layer: intent handling (count/rank/compare/exclude/shortlist), JD parsing for the chat path, reference resolution ("those two", "#3"), and markdown rendering. |

### 4. Guardrail plane — cross-cutting

Not a pipeline stage but a set of invariants enforced *inside* the other planes.
See [guardrails.md](guardrails.md). In short: model-directed text is neutralised
before the model sees it, and graded so only high-confidence attacks hold a
candidate for review; protected attributes are never requested and are scrubbed;
every displayed claim is quote-verified; the final score is deterministic; and
adverse-impact reporting + safe logging sit at the interface plane.

---

## Request flow

A batch is **analysed once**; the extracted profiles then live in an ephemeral
session so the client can score them against many job descriptions and ask
follow-up questions without re-parsing.

```mermaid
%%{init: {'theme':'base','themeVariables':{'fontFamily':'ui-monospace, Menlo, Consolas, monospace','actorBkg':'#151517','actorBorder':'#2C2C31','actorTextColor':'#E6E6E9','signalColor':'#57575E','signalTextColor':'#A9A9B0','noteBkgColor':'#181733','noteBorderColor':'#6E6AF6','noteTextColor':'#E6E6E9','sequenceNumberColor':'#0B0B0C','lineColor':'#57575E','labelBoxBkgColor':'#151517','labelBoxBorderColor':'#2C2C31','labelTextColor':'#E6E6E9','loopTextColor':'#A9A9B0'}}}%%
sequenceDiagram
    autonumber
    participant C as Client
    participant API as FastAPI
    participant W as Background worker
    participant P as Pipeline
    participant S as Session store

    C->>API: POST /sessions/{id}/analyze (files / ZIP)
    API-->>C: 202 Accepted (job_id)
    API->>W: submit batch (thread pool)
    W->>P: ingest, acquire, segment, extract, dedup
    Note over P: injection screen + anchors + quote-verification + gating
    P->>S: publish candidate profiles (status = ready)
    loop until ready
        C->>API: GET /sessions/{id}/status
        API-->>C: progress, then evidence-cited roster
    end
    C->>API: POST /sessions/{id}/score (job description)
    API->>S: read profiles
    API->>P: rubric, judge, aggregate, rank, render
    API-->>C: fit report (matched / missing / citations / ranking)
    C->>API: POST /sessions/{id}/audit (group labels + selection)
    API-->>C: protected-leakage report + four-fifths analysis
    C->>API: POST /sessions/{id}/reset
    API-->>C: session dropped (PII erased)
```

---

## The model backend boundary

Every text-model call goes through `llm.chat_json` and every embedding through
`embeddings.embed`, so the backend is chosen inside those two functions and no
call site changes. (The vision model in `extraction/vlm.py` calls Ollama
directly; it is skipped in `mock` mode.)

```mermaid
%%{init: {'theme':'base','themeVariables':{'fontFamily':'ui-monospace, Menlo, Consolas, monospace','primaryColor':'#151517','primaryBorderColor':'#2C2C31','primaryTextColor':'#E6E6E9','lineColor':'#57575E','clusterBkg':'#0C0C0E','clusterBorder':'#242429','titleColor':'#9A9AA2'}}}%%
flowchart LR
    x1["extract.py"] --> cj["llm.chat_json(task=...)"]
    x2["judge.py"] --> cj
    x3["rubric.py"] --> cj
    x4["query/jd.py"] --> cj
    x5["match.py / query/scoring.py"] --> em["embeddings.embed"]
    cj --> sw{"CV_LLM_MODE"}
    em --> sw
    sw -->|mock| mk["mock.py<br/>deterministic parser"]
    sw -->|ollama| ol["Ollama<br/>qwen3.6 · bge-m3 · qwen2.5vl"]
```

`chat_json` carries a `task` tag (`extract` / `judge` / `rubric` / `jd`) so the
mock backend can apply the right deterministic parser. This is what makes the
whole system runnable with zero models — and what makes CI able to exercise the
complete path deterministically. See
[design-decisions.md](design-decisions.md) for why this boundary was drawn here.

---

## Data & trust flow, end to end

1. **Interface** authenticates, caps input, and hands the batch to the worker.
2. **Extraction** produces profiles in which *every surfaced fact is either
   deterministic (anchors, years) or quote-verified (model prose)*. The injection
   screen has already removed adversarial text; protected attributes were never
   requested.
3. **Scoring** turns a JD into a rubric, judges each requirement with cited
   evidence, and aggregates deterministically into a ranked report.
4. **Guardrails** ensure that at no point does an unverified model claim, an
   adversarial instruction, or a protected attribute influence the result — and
   adverse-impact reporting lets the operator check the *outcome* for potential
   adverse impact, as input to a bias audit.

The result is a report that a human reviewer, an auditor, or a regulator can
follow claim-by-claim back to the source document.
