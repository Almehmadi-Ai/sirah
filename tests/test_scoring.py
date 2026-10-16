"""Unit tests for the deterministic scoring plane (no model calls needed).

Fixtures are synthetic and generic (invented roles and skills). Matching runs in
literal-only mode (empty embedding vectors)."""

from __future__ import annotations

import copy

from app.reasons import describe
from app.scoring import engine, match, report
from app.scoring.match import (
    _degree_rank,
    _domain_terms,
    _edu_field_terms,
    _match_education,
    _match_years,
)
from app.scoring.rubric import _clean_keywords, _short_label


# ---------------------------------------------------------------- fixtures
def _req(
    id_,
    text,
    kind="required",
    rtype="skill",
    keywords=None,
    min_years=None,
    degree_level="",
    equiv=False,
):
    r = {
        "id": id_,
        "text": text,
        "kind": kind,
        "rtype": rtype,
        "min_years": min_years,
        "degree_level": degree_level,
        "equivalent_experience_ok": equiv,
        "keywords": keywords or [],
    }
    r["label"] = _short_label(r)
    return r


RUBRIC = {
    "role_title": "Widget Process Supervisor",
    "clarity": "ok",
    "clarity_reason": "",
    "requirements": [
        _req("r1", "5+ years supervising production lines", rtype="experience_years", min_years=5),
        _req(
            "r2",
            "Bachelor degree in industrial engineering or equivalent experience",
            rtype="education",
            degree_level="bachelor",
            equiv=True,
        ),
        _req("r3", "Lean manufacturing methods", keywords=["Lean", "Kaizen"]),
        _req("r4", "Quality auditing", keywords=["Quality Audit", "ISO 9001"]),
        _req(
            "r5",
            "Forklift certification",
            kind="preferred",
            rtype="certification",
            keywords=["Forklift"],
        ),
        _req("r6", "Excellent teamwork", rtype="soft", keywords=["teamwork"]),
    ],
    "jd_hash": "cafe0123",
}


def _profile(no, name, years, skills_text, education=None):
    pages = [
        {"page": 1, "text": f"{name}\n{skills_text}", "source": "text", "ocr_confidence": None}
    ]
    return {
        "candidate_id": f"c_{no:03d}",
        "display_no": no,
        "identity": {
            "full_name": {"value": name, "evidence": None},
            "emails": [f"p{no}@example.test"],
            "phones": [],
            "links": [],
        },
        "total_years_experience": {"value": years, "basis": "test"},
        "experiences": [
            {
                "title": "Production Line Supervisor",
                "organization": "Plant A",
                "start": "2018",
                "end": "2024",
                "evidence": None,
            }
        ],
        "education": education or [],
        "skills": [
            {"name": s.strip(), "source": "stated", "evidence": None}
            for s in skills_text.split(",")
            if s.strip()
        ],
        "certifications": [],
        "languages": [],
        "extraction": {
            "status": "OK",
            "reasons": [],
            "field_coverage": 0.9,
            "ocr_used": False,
            "vlm_used": False,
            "mean_ocr_confidence": None,
        },
        "duplicate_of": None,
        "_pages": pages,
    }


def _verdicts(profile, rubric=RUBRIC):
    nreq = len(rubric["requirements"])
    return match.match_candidate(profile, rubric, [None] * nreq, {})


# ---------------------------------------------------------------- matching
def test_years_met_partial_missing_unverified():
    v = _verdicts(_profile(1, "Alpha One", 7, "Lean"))
    assert v[0]["verdict"] == "met"
    v = _verdicts(_profile(2, "Beta Two", 4, "Lean"))  # 4 vs 5 -> partial (>=75%)
    assert v[0]["verdict"] == "partial"
    v = _verdicts(_profile(3, "Gamma Three", 1, "Lean"))
    assert v[0]["verdict"] == "missing"
    v = _verdicts(_profile(4, "Delta Four", None, "Lean"))
    assert v[0]["verdict"] == "unverified"


def test_education_equivalent_and_degree():
    edu = [
        {
            "degree": "BSc",
            "field": "Industrial Engineering",
            "institution": "Uni",
            "year": "2015",
            "evidence": {"page": 1, "quote": "BSc Industrial Engineering"},
        }
    ]
    v = _verdicts(_profile(1, "Alpha One", 8, "Lean", education=edu))
    assert v[1]["verdict"] == "met"
    # no degree but long tenure + JD allows equivalent -> partial
    v = _verdicts(_profile(2, "Beta Two", 8, "Lean"))
    assert v[1]["verdict"] == "partial"


def test_term_match_word_boundary_and_soft():
    v = _verdicts(_profile(1, "Alpha One", 8, "Lean, ISO 9001"))
    assert v[2]["verdict"] == "met"  # Lean evidenced
    assert v[3]["verdict"] == "met"  # ISO 9001 evidenced
    assert v[5]["verdict"] == "not_assessable"
    # 'Lean' must not match inside 'cLEANing'
    p = _profile(2, "Beta Two", 8, "industrial cleaning services")
    p["skills"] = []
    v = _verdicts(p)
    assert v[2]["verdict"] == "missing"


def test_floorless_experience_never_blanket_met():
    # a years requirement with no floor must not be satisfied by mere tenure
    req = _req("rx", "Litigation experience", rtype="experience_years", min_years=None)
    rub = {"role_title": "X", "clarity": "ok", "requirements": [req], "jd_hash": "t"}
    p = _profile(1, "Alpha One", 20, "florist, gardening")
    v = match.match_candidate(p, rub, [None], {})
    assert v[0]["verdict"] in ("unverified", "missing")  # never 'met'


def test_certification_absent_section_is_unverified_not_missing():
    req = _req(
        "rc",
        "License to practice law",
        rtype="certification",
        keywords=["bar admission", "law license"],
    )
    rub = {"role_title": "X", "clarity": "ok", "requirements": [req], "jd_hash": "t"}
    p = _profile(2, "Beta Two", 10, "Litigation, Legal research")  # no certs parsed
    v = match.match_candidate(p, rub, [None], {})
    assert v[0]["verdict"] == "unverified"
    p2 = _profile(3, "Gamma Three", 10, "Litigation")
    p2["certifications"] = [{"name": "Notary Commission", "evidence": None}]
    v2 = match.match_candidate(p2, rub, [None], {})
    assert v2[0]["verdict"] == "missing"  # certs parsed, none match -> definitive


def test_certification_never_semantic_partial():
    """A certification needs literal evidence: "CPA" must not partially match
    the job title "Accountant" through embedding similarity."""
    from app.scoring.match import match_candidate

    rub = {
        "requirements": [
            {
                "id": "r1",
                "kind": "preferred",
                "rtype": "certification",
                "label": "CPA",
                "text": "CPA certification",
                "keywords": ["CPA", "Certified Public Accountant"],
            }
        ]
    }
    prof = {
        "skills": [{"name": "Accountant", "source": "stated"}],
        "experiences": [{"title": "Accountant"}],
        "education": [],
        "certifications": [],
        "_pages": [
            {"page": 1, "text": "Senior Accountant with reconciliation and payroll experience"}
        ],
    }
    # strong semantic vectors that WOULD partial-match Accountant~CPA if allowed
    req_vecs = [[[1.0, 0.0]]]
    phrase_vecs = {"accountant": [0.99, 0.14]}  # cos ~0.99 with the req vector
    v = match_candidate(prof, rub, req_vecs, phrase_vecs)[0]
    assert v["verdict"] != "partial"  # NOT a semantic partial
    assert v["verdict"] in ("unverified", "missing")


def test_certification_literal_match_is_met():
    from app.scoring.match import match_candidate

    rub = {
        "requirements": [
            {
                "id": "r1",
                "kind": "preferred",
                "rtype": "certification",
                "label": "CPA",
                "text": "CPA",
                "keywords": ["CPA"],
            }
        ]
    }
    prof = {
        "skills": [],
        "experiences": [],
        "education": [],
        "certifications": [{"name": "CPA"}],
        "_pages": [{"page": 1, "text": "Certifications: CPA (2018)"}],
    }
    v = match_candidate(prof, rub, [None], {})[0]
    assert v["verdict"] == "met"  # literal CPA still matches


# ---------------------------------------------------------------- matching: degree level and field
def test_diploma_is_not_a_masters():
    assert _degree_rank("Diploma in Culinary Arts") == 0
    assert _degree_rank("Diploma") == 0
    assert _degree_rank("BA History") == 1
    assert _degree_rank("MBA, Finance") == 2
    assert _degree_rank("Alabama Institute certificate") is None  # 'ba' inside a word
    assert _degree_rank("Ph.D in Physics") == 3


def _edu_profile(degree, field=""):
    return {
        "education": [
            {
                "degree": degree,
                "field": field,
                "institution": "U",
                "evidence": {"page": 1, "quote": degree},
            }
        ],
        "total_years_experience": {"value": 7.0},
        "_pages": [],
    }


CS_REQ = {
    "degree_level": "bachelor",
    "equivalent_experience_ok": False,
    "text": "Bachelor's degree in Computer Science or a closely related field",
    "keywords": ["Bachelor's degree in Computer Science", "Computer Science", "CS"],
}


def test_education_field_terms_extracted():
    terms = _edu_field_terms(CS_REQ)
    assert any("computer science" == t.lower() for t in terms)


def test_wrong_field_degree_is_partial_not_met():
    v = _match_education(_edu_profile("Bachelor of Arts in History"), CS_REQ)
    assert v["verdict"] == "partial"
    assert "field" in v["detail"].lower()


def test_right_field_degree_is_met():
    v = _match_education(_edu_profile("BSc Computer Science"), CS_REQ)
    assert v["verdict"] == "met"


def test_level_only_requirement_accepts_any_field():
    req = {
        "degree_level": "bachelor",
        "equivalent_experience_ok": False,
        "text": "Bachelor's degree",
        "keywords": ["Bachelor's degree"],
    }
    v = _match_education(_edu_profile("Bachelor of Arts in History"), req)
    assert v["verdict"] == "met"  # no field named by the JD -> level satisfies


def test_diploma_below_bachelor_is_missing():
    v = _match_education(_edu_profile("Diploma"), CS_REQ)
    assert v["verdict"] == "missing"


# ---------------------------------------------------------------- matching: domain-qualified years
YRS_REQ = {
    "min_years": 4.0,
    "text": "4+ years of professional software development experience",
    "keywords": ["professional software development experience"],
}


def _exp_profile(total, titles):
    return {
        "total_years_experience": {"value": total},
        "experiences": [{"title": t, "start": "2019", "end": "Present"} for t in titles],
    }


def test_domain_terms_extracted():
    assert any("software development" in t.lower() for t in _domain_terms(YRS_REQ))


def test_sales_tenure_does_not_meet_software_years():
    v = _match_years(_exp_profile(11.0, ["Sales Manager", "Sales Executive"]), YRS_REQ)
    assert v["verdict"] == "partial"  # partial, never met
    assert "total" in v["detail"]


def test_software_titles_fully_meet_software_years():
    v = _match_years(_exp_profile(7.0, ["Software Developer"]), YRS_REQ)
    assert v["verdict"] == "met"


def test_plain_tenure_requirement_counts_every_role():
    req = {"min_years": 5.0, "text": "5+ years of experience", "keywords": []}
    v = _match_years(_exp_profile(8.0, ["Anything"]), req)
    assert v["verdict"] == "met"


# ---------------------------------------------------------------- engine
def _score(profile):
    return engine.score_candidate(RUBRIC, _verdicts(profile))


def test_full_match_scores_high_and_missing_low():
    edu = [
        {
            "degree": "Bachelor of Industrial Engineering",
            "field": "",
            "institution": "U",
            "year": "",
            "evidence": None,
        }
    ]
    good = _score(
        _profile(1, "Alpha One", 9, "Lean, Kaizen, Quality Audit, Forklift", education=edu)
    )
    assert good["scored"] and good["score"] >= 90 and good["level"] == "strong"
    bad = _score(_profile(2, "Beta Two", 9, "florist, gardening"))
    assert bad["scored"] and bad["level"] == "weak"  # no core capability


def test_hard_minimum_gate_caps_score():
    edu = [{"degree": "BSc", "field": "IE", "institution": "U", "year": "", "evidence": None}]
    # all skills present but only 1 year of experience -> hard-min missing -> cap
    r = _score(
        _profile(3, "Gamma Three", 1, "Lean, Kaizen, Quality Audit, ISO 9001", education=edu)
    )
    assert r["scored"] and r["score"] <= engine.CAP_HARD_MISS
    assert any(c["cap"] == "hard_minimum_missing" for c in r["caps"])


def test_unverified_excluded_not_zeroed():
    # years unreadable -> requirement excluded from denominator, disclosed
    edu = [{"degree": "BSc", "field": "IE", "institution": "U", "year": "", "evidence": None}]
    r = _score(_profile(4, "Delta Four", None, "Lean, Kaizen, Quality Audit", education=edu))
    assert r["scored"]
    assert any("yrs" in u or "experience" in u for u in r["unverified"])
    # excluded from the denominator (not zeroed) BUT capped below Strong:
    # missing information must never out-rank verified candidates.
    assert 60 <= r["score"] <= engine.CAP_UNVERIFIED_REQ
    assert any(c["cap"] == "required_unverified" for c in r["caps"])


def test_ranking_deterministic_with_tiebreaks():
    results = [
        {
            "scored": True,
            "score": 80,
            "req_met_count": 3,
            "years": 6.0,
            "field_coverage": 0.9,
            "display_no": 2,
            "name": "B",
        },
        {
            "scored": True,
            "score": 80,
            "req_met_count": 3,
            "years": 9.0,
            "field_coverage": 0.9,
            "display_no": 1,
            "name": "A",
        },
        {
            "scored": True,
            "score": 91,
            "req_met_count": 4,
            "years": 3.0,
            "field_coverage": 0.5,
            "display_no": 3,
            "name": "C",
        },
    ]
    ranked = engine.rank(copy.deepcopy(results))
    assert [r["name"] for r in ranked] == ["C", "A", "B"]  # years orders within the 80-tie
    assert [r["rank"] for r in ranked] == [1, 2, 2]  # competition ranking: tie shares a rank
    assert not ranked[0]["tied"] and ranked[1]["tied"] and ranked[2]["tied"]


RUB = {
    "requirements": [
        {"id": "r1", "kind": "required", "rtype": "experience_years", "label": "5+ yrs"},
        {"id": "r2", "kind": "required", "rtype": "skill", "label": "Recruitment"},
        {"id": "r3", "kind": "required", "rtype": "skill", "label": "HRIS"},
    ]
}


def test_required_unverified_capped_below_strong():
    va = [
        {"req_id": "r1", "verdict": "unverified"},
        {"req_id": "r2", "verdict": "met"},
        {"req_id": "r3", "verdict": "met"},
    ]
    res = engine.score_candidate(RUB, va)
    assert res["scored"] and res["score"] <= engine.CAP_UNVERIFIED_REQ
    assert res["level"] != "strong"
    assert any(c["cap"] == "required_unverified" for c in res["caps"])


def _mk(no, score, met=3):
    return {
        "candidate_id": f"c_{no:03d}",
        "display_no": no,
        "name": f"P{no}",
        "years": 5.0,
        "field_coverage": 0.8,
        "scored": True,
        "score": score,
        "level": engine.level_for(score),
        "matched": [],
        "missing_required": [],
        "unverified": [],
        "caps": [],
        "req_met_count": met,
        "included_requirements": 3,
        "total_assessable": 3,
        "verifiable_fraction": 1.0,
    }


def test_tied_scores_share_rank():
    ranked = engine.rank([_mk(1, 84), _mk(2, 84), _mk(3, 70)])
    assert [r["rank"] for r in ranked] == [1, 1, 3]
    assert ranked[0]["tied"] and ranked[1]["tied"] and not ranked[2]["tied"]


# ---------------------------------------------------------------- report
def _ranked_fixture():
    edu = [{"degree": "BSc", "field": "IE", "institution": "U", "year": "", "evidence": None}]
    profs = [
        _profile(1, "Alpha One", 9, "Lean, Kaizen, Quality Audit, Forklift", education=edu),
        _profile(2, "Beta Two", 6, "Lean, Quality Audit", education=edu),
        _profile(3, "Gamma Three", 8, "florist, gardening"),
    ]
    results = []
    for p in profs:
        res = engine.score_candidate(RUBRIC, _verdicts(p))
        results.append(
            {
                "candidate_id": p["candidate_id"],
                "display_no": p["display_no"],
                "name": p["identity"]["full_name"]["value"],
                "years": p["total_years_experience"]["value"],
                "field_coverage": 0.9,
                **res,
            }
        )
    return engine.rank(results)


def test_report_render_deterministic_and_clean():
    ranked = _ranked_fixture()
    review = [
        {
            "name": "Epsilon Five",
            "display_no": 9,
            "kind": "needs_review",
            "flags": ["LOW_OCR_CONFIDENCE"],
        }
    ]
    md1 = report.render(RUBRIC, ranked, review, lang="en")
    md2 = report.render(RUBRIC, ranked, review, lang="en")
    assert md1 == md2  # byte-reproducible
    assert md1.startswith("# HR Fit Scoring Results")
    assert "| Rank |" in md1 and "Needs Review" in md1
    assert "Decision support for HR" in md1
    low = md1.lower()
    for leak in ("let's", "wait,", "re-check", "i will", "score calculation"):
        assert leak not in low
    # arabic variant renders with arabic headings + same table shape
    ar = report.render(RUBRIC, ranked, review, lang="ar")
    assert ar.count("|") >= md1.count("|") - 10 and "الأدلة" in ar


def test_table_shows_all_up_to_50_then_caps():
    # policy: all rows when <=50 scored; Top 50 + hidden-note when more
    assert report.top_n_for(5, None) == 5
    assert report.top_n_for(43, None) == 43
    assert report.top_n_for(50, None) == 50
    assert report.top_n_for(51, None) == 50
    assert report.top_n_for(200, None) == 50
    assert report.top_n_for(200, 10) == 10  # explicit override kept
    # 15 scored -> all 15 rows, no caption, no note
    ranked = engine.rank(
        [dict(r, display_no=i + 1, rank=None) for i, r in enumerate(_ranked_fixture() * 5)]
    )
    md = report.render(RUBRIC, ranked, [], lang="en")
    assert md.count("| ") and "Top " not in md.split("##")[1]
    assert "not shown in this summary" not in md
    # 60 scored -> 50 rows + caption + hidden note under the table
    big = engine.rank(
        [dict(r, display_no=i + 1, rank=None) for i, r in enumerate(_ranked_fixture() * 20)]
    )
    md2 = report.render(RUBRIC, big, [], lang="en")
    assert "Top 50 of 60 scored candidates" in md2
    assert "Additional lower-ranked candidates are not shown" in md2
    rows = [ln for ln in md2.split(chr(10)) if ("█" in ln or "░" in ln) and ln.count("|") >= 7]
    assert len(rows) == 50
    ar = report.render(RUBRIC, big, [], lang="ar")
    assert "لا تُعرض بقية السير" in ar


def test_not_scorable_message():
    md = report.render_not_scorable("only a job title was provided", "en")
    assert "could not be scored" in md


def test_partial_marker_in_report():
    req_years = _req("r1", "5+ years", rtype="experience_years", min_years=5)
    rub = {
        "role_title": "X",
        "clarity": "ok",
        "requirements": [req_years, _req("r2", "Lean", keywords=["Lean"])],
        "jd_hash": "t",
    }
    p = _profile(1, "Alpha One", 4, "Lean")  # 4 vs 5 -> partial years
    res = engine.score_candidate(rub, match.match_candidate(p, rub, [None, None], {}))
    ranked = engine.rank(
        [
            {
                "candidate_id": "c1",
                "display_no": 1,
                "name": "Alpha One",
                "years": 4.0,
                "field_coverage": 0.9,
                **res,
            }
        ]
    )
    md = report.render(rub, ranked, [], lang="en")
    assert "≈ 5+ yrs experience" in md and "≈ marks a partially-met" in md


def test_matched_cells_deduped():
    assert report._cells(["A", "A", "B"], 6, "None") == "A, B"


def test_report_declares_tie_and_no_unique_top():
    ranked = engine.rank([_mk(1, 84), _mk(2, 84)])
    md = report.render({"role_title": "X", "requirements": []}, ranked, [], "en")
    assert "tie at the top" in md
    assert "| 1= |" in md
    assert "Top match: **P1**" not in md


def test_weak_pool_not_recommended_for_interview():
    ranked = engine.rank([_mk(1, 22), _mk(2, 15)])
    md = report.render({"role_title": "X", "requirements": []}, ranked, [], "en")
    section = md.split("## Interview Shortlist")[1]
    assert "No candidate reaches an interview-ready fit" in section
    assert "ranks first" not in section


def test_reason_phrases_map():
    assert "readable" in describe(["SPARSE_TEXT"], "en")
    assert "could not be read" in describe(["UNREADABLE_FILE"], "en")
    assert "may not be a CV" in describe(["NO_IDENTITY_ANCHOR"], "en")
    assert "SPARSE_TEXT" not in describe(["SPARSE_TEXT"], "en")  # no raw codes


def test_review_section_uses_phrases_and_kpi_line_renders():
    ranked = engine.rank([_mk(1, 90)])
    lane = [{"name": "Hani", "display_no": 7, "kind": "needs_review", "flags": ["SPARSE_TEXT"]}]
    md = report.render({"role_title": "X", "requirements": []}, ranked, lane, "en", dup_count=2)
    assert "very little content" in md and "SPARSE_TEXT" not in md
    assert (
        "Scored: 1" in md
        and "Needs review (not scored): 1" in md
        and "Duplicates excluded: 2" in md
    )


def test_facts_appendix_cites_evidenced_skill_not_label():
    """Follow-up facts cite the skill that was evidenced, not the requirement's
    alternatives label ("Python/Java" when only Python is in the CV)."""
    rub = {"role_title": "Software Engineer", "requirements": []}
    ranked = [
        {
            "rank": 1,
            "display_no": 3,
            "name": "Mason Vance",
            "score": 69,
            "level": "good",
            "tied": False,
            "req_met_count": 5,
            "total_assessable": 10,
            "missing_required": ["Design backend services"],
            "matched": [
                {"label": "Python/Java", "verdict": "met", "detail": "evidenced: Python"},
                {"label": "SQL databases/SQL", "verdict": "met", "detail": "evidenced: SQL"},
                {
                    "label": "Bachelor degree",
                    "verdict": "met",
                    "detail": "degree evidenced: BA in Computer Science",
                },
            ],
        }
    ]
    txt = report.facts_appendix(rub, ranked, [])
    assert "evidenced: Python," in txt  # the concrete skill
    assert "Python/Java" not in txt  # NOT the alternatives-label
    assert "BA in Computer Science" in txt  # concrete degree, not "Bachelor degree"


# ---------------------------------------------------------------- rubric
def test_keyword_cleaning_and_labels():
    ks = _clean_keywords(
        "Strong experience with Java, Go, Python, or C#", ["Java", "Go", "Python", "C#", "Java", ""]
    )
    assert ks[:4] == ["Java", "Go", "Python", "C#"] and len(ks) == 4
    lab = _short_label(_req("rX", "x", rtype="experience_years", min_years=8))
    assert lab == "8+ yrs experience"


def test_rubric_dedupes_oversplit_requirements():
    # The JD parser (LLM) sometimes over-splits ONE line into several near-
    # identical rows that all render as the SAME label (observed live: a single
    # "Bachelor's degree in Business, Marketing, or a related field" becoming
    # three education rows all labelled "Bachelor degree"). Left unmerged this
    # inflates the denominator and lets the same label show as met AND missing.
    from app.scoring.rubric import _dedupe_requirements

    reqs = [
        _req(
            "r1",
            "Bachelor's degree",
            rtype="education",
            degree_level="bachelor",
            keywords=["Bachelor", "degree"],
        ),
        _req(
            "r2",
            "degree in Business",
            rtype="education",
            degree_level="bachelor",
            keywords=["Business"],
        ),
        _req(
            "r3",
            "degree in Marketing or a related field",
            rtype="education",
            degree_level="bachelor",
            keywords=["Marketing"],
        ),
        _req("r4", "CRM system", keywords=["CRM", "Salesforce"]),
        _req("r5", "negotiation", keywords=["negotiation"]),
    ]
    out = _dedupe_requirements(reqs)
    labels = [r["label"] for r in out]
    assert len(out) == 3, labels  # 3 education rows -> 1
    assert labels.count("Bachelor degree") == 1  # no duplicate label
    edu = next(r for r in out if r["rtype"] == "education")
    # keywords from all three merged rows are preserved (union)
    assert {"Bachelor", "Business", "Marketing"}.issubset(set(edu["keywords"]))
    # ids are re-sequenced contiguously after the merge
    assert [r["id"] for r in out] == ["r1", "r2", "r3"]


def test_rubric_dedupe_keeps_distinct_labels_and_strongest_kind():
    # Distinct requirements must NOT be merged, and a required duplicate must win
    # over a preferred one (a candidate can't benefit from the split).
    from app.scoring.rubric import _dedupe_requirements

    reqs = [
        _req("r1", "Python", keywords=["Python"], kind="preferred"),
        _req("r2", "Python", keywords=["Python", "py"], kind="required"),
        _req("r3", "SQL", keywords=["SQL"]),
    ]
    out = _dedupe_requirements(reqs)
    assert len(out) == 2  # Python x2 -> 1, SQL stays
    py = next(r for r in out if "Python" in r["label"] or "python" in r["keywords"][0].lower())
    assert py["kind"] == "required"  # strongest kind wins


def test_arabic_years_floor_coerced(monkeypatch):
    # simulate the raw-model shape: years line classified as 'responsibility'
    from app.pipeline import llm as L
    from app.scoring import rubric as R

    raw = {
        "role_title": "Teacher",
        "clarity": "ok",
        "requirements": [
            {
                "text": "خبرة لا تقل عن 3 سنوات في التدريس",
                "kind": "required",
                "rtype": "responsibility",
                "keywords": ["teaching experience"],
            },
            {
                "text": "Adobe Photoshop",
                "kind": "required",
                "rtype": "skill",
                "keywords": ["Photoshop"],
            },
        ],
    }
    monkeypatch.setattr(L, "chat_json", lambda *a, **k: raw)
    rub = R.build(object(), "JD: teacher role. " + "خبرة لا تقل عن 3 سنوات في التدريس. " + "x" * 20)
    r0 = rub["requirements"][0]
    assert r0["rtype"] == "experience_years" and r0["min_years"] == 3.0
    assert rub["requirements"][1]["rtype"] == "skill"  # untouched
