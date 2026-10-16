# Evaluation strategy

"It works on the demo" is not evidence. This project treats screening quality as
something to be **measured**, with deterministic tests that run in `mock` mode
(no models, no network) plus opt-in live tests against real models.

---

## Test taxonomy

| Layer | What it proves | Runs in |
|---|---|---|
| **Unit** | anchors, dedup, scoring engine, rubric de-dup, rendering, query logic | mock, always |
| **Golden set + harness** | end-to-end extraction correctness, **evidence validity**, **zero protected leakage**, OCR of a scanned CV | mock, always (OCR checks need Tesseract) |
| **Capability tests** | AR/EN/multilingual extraction, OCR-failure degradation, injection, duplicate/same-name, matched/missing, protected-attr exclusion, four-fifths math, human-review gating, determinism | mock, always |
| **API-flow tests** | auth, multipart ZIP → ingest → async status → score → audit → reset | mock, always |
| **Safe-logging tests** | operational logs contain no PII (proven on a real analysis run) | mock, always |
| **Docs-as-tests** | the README table and `docs/example-output.md` are replayed against the service | mock, always |
| **Live tests** (`CV_LIVE_LLM=1`) | extraction/query against a real local model; the golden set grades it too | Ollama, opt-in |

The default run is deterministic and network-free. The live tests are skipped
unless `CV_LIVE_LLM=1`, and the OCR golden checks unless Tesseract is installed
(CI installs it).

---

## The golden set

`tests/make_golden.py` generates synthetic CVs with known ground truth, each
chosen to stress a specific behaviour:

- **clean** — a well-formed CV (baseline).
- **no-email** — contact extraction must fall back to the phone anchor.
- **sparse** — must gate to *Needs Review*, not score.
- **same-name pair** — two different people who share a name must **not** merge.
- **scanned** — image-only, forces the OCR path.
- **multi-CV-in-one-PDF** — must be flagged.
- **duplicate** — must be detected and excluded from ranking.

`tests/test_golden.py` runs the harness on every build. CI installs Tesseract
with English and Arabic data and sets `CV_REQUIRE_OCR=1`, so the OCR checks on
the scanned CV always run there; on a machine without Tesseract they are skipped
with that reason, and everything else still runs. `python -m tests.eval_golden`
prints the full scorecard (CI does too).

The harness (`tests/eval_golden.py`) asserts, for the whole set:

1. **Extraction correctness** — names, phones, years, skills match the ground
   truth, and a reserved-domain (`example.com`) address is never used as an
   identity.
2. **Evidence validity** — 100%: every cited quote actually occurs on the page it
   is attributed to.
3. **Zero protected leakage** — no protected attribute in any surfaced profile.

## Synthetic corpus (AR/EN)

`scripts/generate_synthetic_data.py` produces a larger, deterministic demo corpus
used by the capability tests: Arabic / English / mixed CVs, plus conflicting
dates, an image-only scan, an exact duplicate, a same-name pair, an embedded
prompt-injection line, a CV carrying protected attributes, and a
fairness-equivalent group — with three job descriptions (Arabic, English,
AI-Engineer) and a ground-truth `manifest.json`. Everything is invented (fictional
names, generic organisations, `example.com` emails, phone numbers from the
fictional `555-01xx` range — see [NOTICE](../NOTICE)).

## Judge calibration

Beyond pass/fail tests, the grounded judge is scored for **agreement** with a
labelled gold set: accuracy and **Cohen's κ**, with a drift alarm when accuracy
drops. This makes judge quality a tracked metric rather than an assumption.

## Coverage

Line/branch coverage is ~80%. The uncovered remainder is concentrated in the
**live-only** code paths (`llm.py`, `vlm.py`, `embeddings.py` — the real Ollama
HTTP calls) which by definition cannot run in mock mode, plus the secondary
multi-turn query and append/merge endpoints. The critical paths — extraction,
grounding, scoring, guardrails, and the API surface — are exercised directly.

## Reproducing the evaluation

```bash
pip install -e ".[dev]"
export CV_API_KEY=dev-key    # CV_LLM_MODE defaults to mock

pytest                       # the full suite, deterministic, no network
coverage run -m pytest && coverage report
python -m tests.eval_golden /tmp/golden   # golden-set scorecard

# opt-in: run the live tests against a local Ollama
CV_LIVE_LLM=1 CV_LLM_MODE=ollama pytest tests/test_extract_live.py
```
