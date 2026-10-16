"""Per-candidate structured extraction.

Pipeline per candidate (one candidate's pages at a time — never cross-candidate):
  0. Prompt-injection screen: model-directed sentences are neutralised first.
  1. Deterministic anchors (email/phone/links/years) — high precision, no LLM.
  2. Local LLM reads prose fields (name/title/skills/education) against a strict
     JSON schema, returning a verbatim ``evidence_quote`` per item.
  3. Quote-verification: WE locate each quote in the candidate's pages and attach
     the page number. Unverifiable quotes are dropped (never displayed) so every
     shown citation is real. The LLM points; the code reads.
  4. Protected attributes are never requested (schema) and defensively scrubbed.
  5. Confidence gating -> status OK | NEEDS_REVIEW with reasons.

Output is a ``CandidateProfile`` (``app/profiles.py``). Private keys (``_pages``)
carry source text for later evidence/QA and are never surfaced to the user.
"""

from __future__ import annotations

from typing import Any, Literal, cast, overload

from ..profiles import (
    CandidateProfile,
    Certification,
    Education,
    Evidence,
    Experience,
    Identity,
    Language,
    Skill,
    Source,
    YearsOfExperience,
)
from . import anchors, injection, llm, names
from .evidence import verify_quote, verify_term
from .llm import pick, pick_list

PROTECTED_KEYS = {
    "gender",
    "sex",
    "age",
    "dob",
    "date_of_birth",
    "birthdate",
    "birth_date",
    "nationality",
    "religion",
    "marital",
    "marital_status",
    "ethnicity",
    "race",
    "photo",
}

EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "full_name": {"type": "string"},
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "total_years_experience": {"type": ["number", "null"]},
        "experiences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "organization": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                },
                "required": ["title", "evidence_quote"],
            },
        },
        "education": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "degree": {"type": "string"},
                    "field": {"type": "string"},
                    "institution": {"type": "string"},
                    "year": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                },
                "required": ["evidence_quote"],
            },
        },
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "source": {"type": "string", "enum": ["stated", "inferred"]},
                    "evidence_quote": {"type": "string"},
                },
                "required": ["name", "source"],
            },
        },
        "certifications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "evidence_quote": {"type": "string"}},
                "required": ["name"],
            },
        },
        "languages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "level": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    "required": ["full_name", "skills", "experiences"],
}

SYSTEM = (
    "You are an HR CV extraction engine. Extract ONLY professional information "
    "explicitly present in the CV text, matching the JSON schema exactly.\n"
    "RULES:\n"
    "- For every experience, education entry and skill, include a short "
    "evidence_quote copied VERBATIM (exact substring) from the CV text so it can "
    "be verified. If you have no verbatim quote, omit that item.\n"
    "- full_name is the candidate's PERSONAL NAME (usually the largest text at the "
    "top). NEVER use a section heading (e.g. 'Work Experience', 'Education', "
    "'Skills', 'Profile', 'Contact') as the name. If unsure, leave full_name empty.\n"
    "- Mark each skill 'stated' if written explicitly, else 'inferred'. Prefer 'stated'.\n"
    "- NEVER output gender, age, date of birth, nationality, religion, marital "
    "status, photo, or ethnicity. Ignore any such details entirely.\n"
    "- Do not invent or assume facts. Leave unknown fields empty/null.\n"
    "- Preserve Arabic text exactly as written."
)


def _candidate_text_for_llm(pages: list[dict[str, Any]], per_page_cap=4000, total_cap=14000) -> str:
    parts: list[str] = []
    total = 0
    for p in pages:
        t = (p.get("text") or "").strip()
        if not t:
            continue
        seg = f"[Page {p['page']}]\n{t[:per_page_cap]}"
        if total + len(seg) > total_cap:
            parts.append(seg[: max(0, total_cap - total)])
            break
        parts.append(seg)
        total += len(seg)
    return "\n\n".join(parts)


def _name_evidence(name: str, pages: list[dict[str, Any]]) -> Evidence | None:
    if not name:
        return None
    return verify_quote(name, pages)


def extract_candidate(candidate: dict[str, Any], idx: int, settings) -> CandidateProfile:
    pages: list[dict[str, Any]] = candidate.get("pages", [])
    full_text: str = candidate.get("full_text", "")
    cid = f"c_{idx:03d}"

    # 0. prompt-injection screen: neutralise model-directed sentences before any
    # model sees the text; the graded result sets the consequence in step 4.
    screening = injection.sanitize(pages, full_text)
    pages, full_text = screening.pages, screening.full_text

    # 1. deterministic anchors
    emails = anchors.find_emails(full_text)
    phones = anchors.find_phones(full_text)
    links = anchors.find_links(full_text)

    # 2. LLM prose fields (skipped gracefully if no text or model down)
    llm_out: dict[str, Any] = {}
    llm_error: str | None = None
    if full_text.strip():
        try:
            llm_out = llm.chat_json(
                settings,
                SYSTEM,
                _candidate_text_for_llm(pages),
                schema=EXTRACTION_SCHEMA,
                task="extract",
            )
        except llm.LLMError as e:  # degrade to deterministic-only, flag it
            llm_error = str(e)

    llm_name = str(pick(llm_out, ["full_name", "name", "candidate_name"])).strip()
    name = llm_name if names.is_valid_name(llm_name) else ""
    if not name:
        heur = names.heuristic_name(pages)
        # if neither the model nor the heuristic yields a VALID name, leave it empty
        # (shown as "(name not detected)") rather than displaying garbage.
        name = heur if names.is_valid_name(heur) else ""
    name = names.normalize_case(name)
    headline = str(pick(llm_out, ["headline", "current_title", "position", "job_title"])).strip()
    summary = str(pick(llm_out, ["summary", "professional_summary", "profile", "about"])).strip()

    experiences = _build_list(
        pick_list(
            llm_out, ["experiences", "experience", "work_experience", "employment", "work_history"]
        ),
        pages,
        kind="experience",
    )
    education = _build_list(
        pick_list(llm_out, ["education", "educations", "academic_background", "academics"]),
        pages,
        kind="education",
    )
    skills = _build_skills(pick_list(llm_out, ["skills", "skill", "skillset", "skill_set"]), pages)
    certifications = _build_list(
        pick_list(llm_out, ["certifications", "certificates", "certs"]),
        pages,
        kind="certification",
    )
    languages = _build_list(pick_list(llm_out, ["languages", "language"]), pages, kind="language")

    # 3. years of experience — deterministic arithmetic over the LLM's dated roles
    ty, n_roles = anchors.compute_total_years(experiences)
    if ty is not None:
        # "Present"/"till date" is treated as TODAY (customer CVs are recent, so
        # an open-ended current role genuinely runs to now). Trust the date math.
        total_years: YearsOfExperience = {
            "value": round(ty, 1),
            "basis": f"computed from {n_roles} dated role(s)",
        }
        # A MAJOR contradiction with the candidate's own stated total is
        # surfaced as a verification note — never a silent change of the
        # authoritative computed value.
        stated = anchors.stated_years_claim(full_text)
        if stated is not None and abs(ty - stated) >= max(2.0, 0.3 * ty):
            total_years["note"] = (
                f"CV itself states ~{stated:g} years; dated roles "
                f"compute {round(ty, 1):g} — verify at interview"
            )
    else:
        lv = pick(
            llm_out,
            ["total_years_experience", "years_of_experience", "total_experience", "total_years"],
            default=None,
        )
        if isinstance(lv, int | float):
            total_years = {"value": float(lv), "basis": "stated in CV"}
        else:
            # deterministic fallback: scan the raw CV text for date ranges / "N years"
            ft = anchors.years_from_text(full_text)
            total_years = (
                {"value": round(ft, 1), "basis": "computed from CV text"}
                if ft is not None
                else {"value": None, "basis": "unknown"}
            )

    identity: Identity = {
        "full_name": {"value": name, "evidence": _name_evidence(name, pages)},
        "emails": emails,
        "phones": phones,
        "links": links,
    }

    # 4. coverage + gating
    coverage = _coverage(name, emails, phones, experiences, skills, education)
    reasons = _gating_reasons(candidate, name, emails, phones, coverage, settings)
    if llm_error:
        reasons.append("LLM_UNAVAILABLE")
    # A high-confidence injection holds the candidate for review; a weaker one is
    # a non-gating advisory and the candidate is still scored.
    advisories: list[str] = []
    if screening.severity == injection.HIGH:
        reasons.append("INJECTION_SUSPECTED")
    elif screening.severity == injection.MEDIUM:
        advisories.append("INJECTION_NEUTRALIZED")
    status = "NEEDS_REVIEW" if reasons else "OK"

    profile: CandidateProfile = {
        "candidate_id": cid,
        "display_no": idx,
        "source": _source(candidate),
        "identity": identity,
        "headline": headline,
        "summary": summary,
        "total_years_experience": total_years,
        "experiences": experiences,
        "education": education,
        "skills": skills,
        "certifications": certifications,
        "languages": languages,
        "extraction": {
            "ocr_used": candidate.get("ocr_used", False),
            "vlm_used": candidate.get("vlm_used", False),
            "mean_ocr_confidence": candidate.get("mean_ocr_confidence"),
            "field_coverage": round(coverage, 2),
            "status": status,
            "reasons": reasons,  # gating: any reason means NEEDS_REVIEW
            "advisories": advisories,  # non-gating: shown to the reviewer, still scored
            "injection": screening.summary(),
        },
        "duplicate_of": None,
        "_excluded_by_policy": sorted(PROTECTED_KEYS),
        # private (never surfaced) — source text for later evidence / QA:
        "_pages": [
            {
                "page": p["page"],
                "text": p.get("text", ""),
                "source": p.get("source"),
                "ocr_confidence": p.get("ocr_confidence"),
            }
            for p in pages
        ],
    }
    return _scrub_protected(profile)


def _source(candidate: dict[str, Any]) -> Source:
    src: Source = {
        "file": candidate.get("document"),
        "page_range": candidate.get("page_range"),
        "container": candidate.get("container"),
    }
    if candidate.get("sha256"):
        src["sha256"] = candidate["sha256"]
    return src


@overload
def _build_list(
    items: Any, pages: list[dict[str, Any]], kind: Literal["experience"]
) -> list[Experience]: ...
@overload
def _build_list(
    items: Any, pages: list[dict[str, Any]], kind: Literal["education"]
) -> list[Education]: ...
@overload
def _build_list(
    items: Any, pages: list[dict[str, Any]], kind: Literal["certification"]
) -> list[Certification]: ...
@overload
def _build_list(
    items: Any, pages: list[dict[str, Any]], kind: Literal["language"]
) -> list[Language]: ...
def _build_list(items: Any, pages: list[dict[str, Any]], kind: str) -> list[Any]:
    out: list[Any] = []
    for it in items or []:
        if isinstance(it, str):  # some models return plain strings
            nm = it.strip()
            if not nm:
                continue
            if kind == "certification":
                out.append({"name": nm, "evidence": verify_term(nm, pages)})
            elif kind == "language":
                out.append({"name": nm, "level": "", "evidence": verify_term(nm, pages)})
            continue
        if not isinstance(it, dict):
            continue
        ev = verify_quote(pick(it, ["evidence_quote", "evidence", "quote"]), pages)
        if kind == "experience":
            title = str(pick(it, ["title", "job_title", "role", "position"])).strip()
            org = str(pick(it, ["organization", "company", "employer", "org"])).strip()
            if not title and not org:
                continue
            out.append(
                {
                    "title": title,
                    "organization": org,
                    "start": str(pick(it, ["start", "start_date", "from", "start_year"])).strip(),
                    "end": str(pick(it, ["end", "end_date", "to", "end_year"])).strip(),
                    "evidence": ev,
                }
            )
        elif kind == "education":
            degree = str(pick(it, ["degree", "qualification"])).strip()
            field = str(pick(it, ["field", "major", "field_of_study", "specialization"])).strip()
            inst = str(pick(it, ["institution", "university", "school", "college"])).strip()
            if not any((degree, field, inst)):
                continue
            out.append(
                {
                    "degree": degree,
                    "field": field,
                    "institution": inst,
                    "year": str(
                        pick(
                            it, ["year", "graduation_year", "grad_year", "completion_year", "date"]
                        )
                    ).strip(),
                    "evidence": ev,
                }
            )
        elif kind == "certification":
            nm = str(pick(it, ["name", "title", "certification"])).strip()
            if nm:
                out.append({"name": nm, "evidence": ev})
        elif kind == "language":
            nm = str(pick(it, ["name", "language"])).strip()
            if nm:
                out.append(
                    {
                        "name": nm,
                        "level": str(pick(it, ["level", "proficiency", "fluency"])).strip(),
                        "evidence": ev,
                    }
                )
    return out


def _build_skills(items: Any, pages: list[dict[str, Any]]) -> list[Skill]:
    """A skill is 'stated' (with evidence) iff its name literally appears in the
    CV text — determined HERE, not from the model's self-label — else 'inferred'."""
    out: list[Skill] = []
    seen = set()
    for it in items or []:
        if isinstance(it, str):
            nm = it.strip()
        elif isinstance(it, dict):
            nm = str(pick(it, ["name", "skill", "skill_name"])).strip()
        else:
            continue
        if not nm or nm.lower() in seen:
            continue
        seen.add(nm.lower())
        ev = verify_term(nm, pages)
        out.append({"name": nm, "source": "stated" if ev else "inferred", "evidence": ev})
    return out


def _coverage(name, emails, phones, experiences, skills, education) -> float:
    cov = 0.0
    if name:
        cov += 0.20
    if emails or phones:
        cov += 0.20
    if experiences:
        cov += 0.25
    if len(skills) >= 3:
        cov += 0.20
    elif skills:
        cov += 0.10
    if education:
        cov += 0.15
    return cov


def _gating_reasons(candidate, name, emails, phones, coverage, settings) -> list[str]:
    reasons: list[str] = []
    if candidate.get("unreadable") or (
        candidate.get("page_count", 0) == 0 and candidate.get("text_chars", 0) == 0
    ):
        # A genuinely unreadable file is its OWN reason — never described
        # to the customer the same way as a readable-but-sparse CV.
        return ["UNREADABLE_FILE"]
    if not (emails or phones or name):
        reasons.append("NO_IDENTITY_ANCHOR")
    moc = candidate.get("mean_ocr_confidence")
    if candidate.get("ocr_used") and moc is not None and moc < settings.ocr_min_confidence:
        reasons.append("LOW_OCR_CONFIDENCE")
    if candidate.get("text_chars", 0) < 200:
        reasons.append("SPARSE_TEXT")
    if coverage < 0.40:
        reasons.append("SPARSE_EXTRACTION")
    if candidate.get("multi_cv"):
        reasons.append("MULTIPLE_CVS_IN_FILE")
    return reasons


def error_profile(candidate: dict[str, Any], idx: int) -> CandidateProfile:
    """Minimal NEEDS_REVIEW profile for a candidate whose extraction crashed —
    the file stays visible and counted (review lane) instead of killing the
    whole batch."""
    return {
        "candidate_id": f"c_{idx:03d}",
        "display_no": idx,
        "source": _source(candidate),
        "identity": {
            "full_name": {"value": "", "evidence": None},
            "emails": [],
            "phones": [],
            "links": [],
        },
        "headline": "",
        "total_years_experience": {"value": None, "basis": "unknown"},
        "experiences": [],
        "education": [],
        "skills": [],
        "certifications": [],
        "languages": [],
        "extraction": {
            "ocr_used": bool(candidate.get("ocr_used")),
            "vlm_used": bool(candidate.get("vlm_used")),
            "mean_ocr_confidence": candidate.get("mean_ocr_confidence"),
            "field_coverage": 0.0,
            "status": "NEEDS_REVIEW",
            "reasons": ["UNREADABLE_FILE"],
            "advisories": [],
            "injection": {"severity": injection.NONE, "categories": []},
        },
        "duplicate_of": None,
        "_excluded_by_policy": sorted(PROTECTED_KEYS),
        "_pages": [],
    }


def _scrub_protected(profile: CandidateProfile) -> CandidateProfile:
    """Defensive: drop any protected keys that might have slipped into nested dicts."""

    def clean(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                k: clean(v)
                for k, v in obj.items()
                if k == "_excluded_by_policy" or k.lower() not in PROTECTED_KEYS
            }
        if isinstance(obj, list):
            return [clean(v) for v in obj]
        return obj

    return cast(CandidateProfile, clean(profile))
