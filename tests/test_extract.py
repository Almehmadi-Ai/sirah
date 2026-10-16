"""Extraction units that need no model: protected-attribute scrubbing and the
fallback profile for a candidate whose extraction failed."""

from app.pipeline import extract


def test_protected_scrub_removes_stray_keys():
    p = extract._scrub_protected(
        {"identity": {"full_name": "x", "nationality": "Y"}, "_excluded_by_policy": ["nationality"]}
    )
    assert "nationality" not in p["identity"]
    assert p["_excluded_by_policy"] == ["nationality"]


def test_error_profile_is_reviewable():
    p = extract.error_profile({"document": "x.pdf", "page_range": [0, 0]}, 3)
    assert p["extraction"]["status"] == "NEEDS_REVIEW"
    assert p["extraction"]["reasons"] == ["UNREADABLE_FILE"]
    assert p["candidate_id"] == "c_003"
