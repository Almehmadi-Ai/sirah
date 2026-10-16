"""Evidence grounding: locate a claim in the candidate's own pages.

The model may propose a quote or a skill name; code decides whether it is really
in the CV and on which page. The model never supplies page numbers.
"""

from __future__ import annotations

import re
from typing import Any

from ..profiles import Evidence

_WORDISH = r"A-Za-z0-9؀-ۿ"  # latin + arabic, for skill-name boundaries


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def verify_quote(quote: str | None, pages: list[dict[str, Any]]) -> Evidence | None:
    """Return {page, quote} if the verbatim quote (>=8 chars) occurs in a page,
    else None. WE assign the page — the model never supplies page numbers."""
    q = (quote or "").strip()
    if len(q) < 8:
        return None
    nq = _norm(q)
    for p in pages:
        if nq in _norm(p.get("text")):
            return {"page": p["page"], "quote": q[:240]}
    return None


def verify_term(term: str | None, pages: list[dict[str, Any]], min_len: int = 2) -> Evidence | None:
    """Word-boundary presence check for a short term (skill name). Returns
    {page, quote} if the term literally appears, else None — so 'R' does not
    match inside 'Recruitment' but 'SAP'/'C++' still match.

    Plural-tolerant on the FINAL word only: 'code review' matches 'code
    reviews' and 'code reviews' matches 'code review' — real CVs freely mix
    singular/plural while boundaries stay strict ('Java' still never matches
    'JavaScript')."""
    t = (term or "").strip()
    if len(t) < min_len:
        return None
    body = re.escape(t)
    if t[-1].isalpha():
        if len(t) > 3 and t.lower().endswith("s") and not t.lower().endswith("ss"):
            body = re.escape(t[:-1]) + r"(?:e?s)?"  # stem + optional plural
        else:
            body = body + r"(?:e?s)?"  # optional plural suffix
    try:
        pat = re.compile(rf"(?<![{_WORDISH}]){body}(?![{_WORDISH}])", re.I)
    except re.error:
        return None
    for p in pages:
        if pat.search(p.get("text") or ""):
            return {"page": p["page"], "quote": t[:120]}
    return None
