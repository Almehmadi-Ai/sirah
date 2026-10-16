# Example output

Everything below is **real output** of the service in the default `mock` mode on
the bundled synthetic corpus, copied verbatim. Only the demo-mode banner that
prefixes every markdown response is omitted here, and one long list in the audit
JSON is shortened (marked `…`). `tests/test_docs_examples.py` re-runs this exact
flow and fails if this page, or the table in the README, drifts from what the
service actually produces.

Reproduce it (see the README's Quick start):

```bash
python scripts/generate_synthetic_data.py
export CV_API_KEY=dev-key        # Windows (PowerShell): $env:CV_API_KEY = "dev-key"
uvicorn app.main:app --port 8089
# upload synthetic_data/bundles/demo_english.zip, then score it against the
# one-line AI-Engineer job description from the README's Quick start
```

No models, no network. The batch holds five fully synthetic candidates, plus an
exact duplicate and a CV carrying a prompt-injection line. (The corpus also ships
Arabic and bilingual CVs, left out here to keep the example readable in
English.)

---

## 1. After upload — the analysed roster

`GET /sessions/{id}/status` returns the roster as `roster_markdown`. Note the
deterministic years, the top skills, and the automatic **duplicate** and
**injection** flags:

> ## 👥 CV Batch Analyzed — 7 candidate(s)
> _1 flagged for review, 1 duplicate(s)._
>
> - **Candidate 1 — David Kim** (10y) · Python, FastAPI, RAG, Agents, SQL
> - **Candidate 2 — Jordan Miller** (8y) · Python, FastAPI, SQL, Docker, scikit-learn
> - **Candidate 3 — Taylor Brooks** (5y) · Python, Java, SQL, Docker
> - **Candidate 4 — Chris Evans** (6y) · Python, SQL, Pandas, Machine Learning
> - **Candidate 5 — Robin Shaw** (5y) · Excel, SQL, Reporting, Process Improvement
> - **Candidate 6 — Jordan Miller** (8y) · Python, FastAPI, SQL, Docker, scikit-learn  ⚠️ _DUPLICATE_
> - **Candidate 7 — Morgan Lee** (7y) · Python, FastAPI, PostgreSQL, Docker  ⚠️ _INJECTION_SUSPECTED_
>
> Ask me anything about this batch: *who has a skill, compare candidates, filter by years, rank for a role, exclude, or build a shortlist.*
>
> ---
> _Decision support for HR — not a final hiring decision._

---

## 2. Fit scoring against a job description

`POST /sessions/{id}/score` with

```json
{"jd_text": "AI Engineer. Required skills: Python, FastAPI, RAG, Agents, SQL, Docker. Minimum 4 years."}
```

returns this `report_md`. Every heading, number and sentence is rendered by
deterministic code from the engine's structured results — the same input always
produces the same bytes:

> # HR Fit Scoring Results
> **Role: AI Engineer - 5 candidates scored.** Top match: **David Kim** - 100% (🟢 Strong Fit).
> _Scored: 5 · Needs review (not scored): 1 · Duplicates excluded: 1._
>
> ## Ranked Fit Table
>
> | Rank | CV # | Name | Fit Score | Level | Matched | Missing |
> |:--:|--:|---|:--:|:--:|---|---|
> | 1 | 1 | **David Kim** | `██████████` **100%** | 🟢 Strong Fit | Python, FastAPI, RAG, Agents, SQL, Docker, … | None |
> | 2 | 2 | **Jordan Miller** | `███████░░░` **71%** | 🟡 Good Fit | Python, FastAPI, SQL, Docker, 4+ yrs experience | RAG, Agents |
> | 3 | 3 | **Taylor Brooks** | `██████░░░░` **57%** | 🟠 Partial Fit | Python, SQL, Docker, 4+ yrs experience | FastAPI, RAG, Agents |
> | 4 | 4 | **Chris Evans** | `████░░░░░░` **43%** | 🟠 Partial Fit | Python, SQL, 4+ yrs experience | FastAPI, RAG, Agents, Docker |
> | 5 | 5 | **Robin Shaw** | `███░░░░░░░` **29%** | 🔴 Weak Fit | SQL, 4+ yrs experience | Python, FastAPI, RAG, Agents, … |
>
> _Fit bands: Strong 80-100 · Good 60-79 · Partial 40-59 · Weak below 40. ≈ marks a partially-met requirement. Scores are computed by a deterministic engine from evidenced requirements._
>
> ---
> ## Evidence - Top Candidates
> **David Kim (CV #1)** - 10 years experience. Python, FastAPI, RAG, Agents, SQL, Docker (p.1).
> **Jordan Miller (CV #2)** - 8 years experience. Python, FastAPI, SQL, Docker, 4+ yrs experience (p.1).
> **Taylor Brooks (CV #3)** - 5 years experience. Python, SQL, Docker, 4+ yrs experience (p.1).
>
> ## Gaps / Risks
> *   **Agents:** not evidenced by 4 of 5 scored candidates.
> *   **RAG:** not evidenced by 4 of 5 scored candidates.
> *   **FastAPI:** not evidenced by 3 of 5 scored candidates.
>
> ## Interview Shortlist
> **David Kim (CV #1)** ranks first at 100% (Strong Fit), meeting 7 of 7 assessable requirements, 10 years experience. **Jordan Miller (CV #2)** follows at 71% with 5 of 7 requirements evidenced.
>
> ## Needs Review - Not Scored
> - **Morgan Lee** (CV #7) - the CV contains instructions aimed at the screening system (removed before analysis); held for manual review instead of being ranked.
>
> _Decision support for HR - not a final hiring decision._

The injected CV is **not** ranked. Its adversarial sentence was neutralised before
any model saw it and, because the screen graded it high-confidence, the candidate
was held for a human instead of entering the queue. (A weaker signal would only
add a review note: the candidate would still be scored.)

---

## 3. Adverse-impact report (four-fifths rule)

`POST /sessions/{id}/audit` always returns the protected-attribute leakage
report. Given operator-supplied group labels and the selection outcome — here a
deliberately tiny, illustrative pair — it also computes per-group selection rates
and four-fifths impact ratios:

```json
{"labels": {"c_001": {"gender": "female"}, "c_002": {"gender": "male"}}, "selected": {"c_001": true, "c_002": false}}
```

```json
{
  "chat_id": "demo",
  "leakage": {
    "protected_leaks": 0,
    "clean": true,
    "details": [],
    "excluded_by_policy": [
      "age",
      "date_of_birth",
      "gender",
      "…"
    ]
  },
  "adverse_impact": {
    "four_fifths_threshold": 0.8,
    "axes": {
      "gender": {
        "groups": {
          "female": {
            "total": 1,
            "selected": 1,
            "selection_rate": 1.0,
            "impact_ratio": 1.0,
            "adverse": false
          },
          "male": {
            "total": 1,
            "selected": 0,
            "selection_rate": 0.0,
            "impact_ratio": 0.0,
            "adverse": true
          }
        },
        "reference_rate": 1.0,
        "adverse_impact": true
      }
    },
    "adverse_impact": true
  }
}
```

`leakage.clean = true` shows no protected attribute reached the scored output; an
`impact_ratio` below 0.8 flags potential adverse impact for review. These are
computations to *support* a bias audit, not an audit: the labels come from the
operator, and a regulated audit (e.g. under NYC Local Law 144) must be performed
by an independent auditor.
