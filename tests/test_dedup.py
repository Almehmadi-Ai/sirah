"""Within-batch duplicate detection: the same content is a duplicate; the same
contact details with different content is a conflicting version for review."""

from app.pipeline import dedup


def test_dedup_by_email_case_insensitive():
    profiles = [
        {
            "candidate_id": "c_001",
            "identity": {"emails": ["a@contoso.com"], "phones": []},
            "duplicate_of": None,
            "extraction": {"status": "OK", "reasons": []},
        },
        {
            "candidate_id": "c_002",
            "identity": {"emails": ["A@Contoso.com"], "phones": []},
            "duplicate_of": None,
            "extraction": {"status": "OK", "reasons": []},
        },
    ]
    dedup.mark_duplicates(profiles)
    assert profiles[1]["duplicate_of"] == "c_001"
    assert "DUPLICATE" in profiles[1]["extraction"]["reasons"]


def _dprof(cid, no, name, email, text):
    return {
        "candidate_id": cid,
        "display_no": no,
        "identity": {"full_name": {"value": name}, "emails": [email], "phones": []},
        "skills": [],
        "extraction": {"status": "OK", "reasons": []},
        "_pages": [{"page": 1, "text": text}],
    }


def test_conflicting_versions_flagged_not_collapsed():
    a = _dprof(
        "c_001",
        1,
        "Rami Khoury",
        "rami@contoso.com",
        "Network engineer Cisco routing firewalls VPN monitoring enterprise",
    )
    b = _dprof(
        "c_002",
        2,
        "Rami Khoury",
        "rami@contoso.com",
        "Senior accountant IFRS reconciliation QuickBooks financial reporting close",
    )
    out = dedup.mark_duplicates([a, b])
    assert out[1].get("duplicate_of") is None
    assert "CONFLICTING_VERSIONS" in out[1]["extraction"]["reasons"]
    assert out[0].get("verification_notes")


def test_identical_content_is_a_duplicate():
    text = "Software engineer Python SQL REST APIs Git Docker backend development services"
    a = _dprof("c_001", 1, "Yousef Nabil", "y@contoso.com", text)
    b = _dprof("c_002", 2, "Yousef Nabil", "y@contoso.com", text)
    out = dedup.mark_duplicates([a, b])
    assert out[1].get("duplicate_of") == "c_001"


# Placeholder contacts (example.com) are not identity keys, so these pairs
# match only on name + skills; content must still separate a duplicate from
# a conflicting version.
def _sales(cid, no, text):
    p = _dprof(
        cid, no, "Yara Sethi", "candidate016@example.com", text
    )  # example.com = placeholder -> no real key
    p["skills"] = [
        {"name": s} for s in ["Prospecting", "Negotiation", "B2B Sales", "Quota Attainment"]
    ]
    return p


def test_fallback_same_name_different_content_is_conflict_not_duplicate():
    a = _sales(
        "c_001",
        1,
        "Sales Executive Larkspur Foods 2025 present closed 18M SAR "
        "pipeline 60 qualified quota 120 percent Vertex Labs Auralink seven years",
    )
    b = _sales(
        "c_002",
        2,
        "Sales Executive Ironwood Manufacturing 2022 present Cobalt Industries "
        "twelve years managed 120 key accounts closed 3M SAR led team",
    )
    out = dedup.mark_duplicates([a, b])
    assert out[1].get("duplicate_of") is None  # NOT excluded as a duplicate
    assert "CONFLICTING_VERSIONS" in out[1]["extraction"]["reasons"]  # flagged for human review
    assert out[0].get("verification_notes")  # kept profile carries a note


def test_fallback_same_name_identical_content_is_a_duplicate():
    text = (
        "Sales Executive Larkspur Foods 2025 present closed 18M SAR pipeline 60 qualified "
        "quota 120 percent Vertex Labs Auralink Cedarfield Group seven years measurable impact"
    )
    a = _sales("c_001", 1, text)
    b = _sales("c_002", 2, text)  # a genuine re-upload of the same document
    out = dedup.mark_duplicates([a, b])
    assert out[1].get("duplicate_of") == "c_001"
