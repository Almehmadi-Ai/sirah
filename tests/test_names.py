"""Person-name heuristics: what is accepted as a candidate's name, and how its
case is normalised for display."""

import pytest

from app.pipeline import names


@pytest.mark.parametrize(
    "text",
    [
        "Anna Gunther",
        "Carole Chun",
        "Mary-Jane O'Neil",  # punctuation inside a real name
        "Dyson Yudiana",
        "Jan van der Berg",  # one uppercase-initial word is enough
        "James Watson",  # a trailing "s" is not a plural heading
        "Shyam Sharma",
        "Cole Hodges",
        "C.V. Raman",  # initials, not the "CV" heading token
        "Jonathan Burns",
    ],
)
def test_accepts_real_names(text):
    assert names.is_valid_name(text)


@pytest.mark.parametrize(
    "text",
    [
        # template placeholders
        "Full Name",
        "Your Name Here",
        "Lorem Ipsum",
        "Applicant Name",
        "CANDIDATE JOBSEEKER",
        "Jane Doe",
        "John Doe",
        # section headings, with a trailing colon or in the plural
        "Technical Skills:",
        "Core Competencies:",
        "DATA SCIENCE RESUMES",
        "Employment Histories",
        "HUMAN RESOURCES CV",
        # lower-case sentence fragments
        "specific requirements.",
        "with over ten years",
        # institutions and honours
        "Cranford High School",
        "Magna Cum Laude",
        "Riverside Academy",
    ],
)
def test_rejects_non_names(text):
    assert not names.is_valid_name(text)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("JESSICA CLAIRE", "Jessica Claire"),
        ("jessica claire", "Jessica Claire"),
        ("ahmed al-otaibi", "Ahmed Al-Otaibi"),
        ("Jessica Claire", "Jessica Claire"),
        ("Ronan McDonald", "Ronan McDonald"),  # mixed-case words are left alone
        ("Sarah O'Brien", "Sarah O'Brien"),
    ],
)
def test_normalize_case(raw, expected):
    assert names.normalize_case(raw) == expected
