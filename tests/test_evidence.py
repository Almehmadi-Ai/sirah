"""Evidence verification: a quote or term counts only where it literally occurs."""

from app.pipeline import evidence


def test_quote_verification():
    pages = [{"page": 1, "text": "Administered SAP SuccessFactors for performance management."}]
    ev = evidence.verify_quote("SAP SuccessFactors", pages)
    assert ev and ev["page"] == 1
    assert evidence.verify_quote("a fabricated quote not present", pages) is None
    assert evidence.verify_quote("short", pages) is None  # below min length


def test_verify_term_plural_tolerance():
    pages = [{"page": 1, "text": "Participated in code reviews and testing."}]
    assert evidence.verify_term("Code review", pages) is not None
    assert (
        evidence.verify_term("code reviews", [{"page": 1, "text": "weekly code review"}])
        is not None
    )
    # boundaries stay strict
    assert evidence.verify_term("Java", [{"page": 1, "text": "JavaScript only"}]) is None
