"""The deterministic (mock) JD parser: requirements are split per sentence, the
years-of-experience floor is read from the sentence that states it, and Arabic
JDs are parsed as well as English ones."""

import pytest

from app import mock


def _p(text):
    return mock._parse_jd(text)


def test_one_line_jd_is_split_into_sentences():
    # the README's curl example
    p = _p(
        "AI Engineer. Required skills: Python, FastAPI, RAG, Agents, SQL, Docker. Minimum 4 years."
    )
    assert p["role_title"] == "AI Engineer"
    assert p["must_have_skills"] == ["Python", "FastAPI", "RAG", "Agents", "SQL", "Docker"]
    assert p["min_years_experience"] == 4


def test_mid_sentence_abbreviation_does_not_split():
    p = _p("Backend Developer. Required skills: Python, web frameworks e.g. Django, SQL.")
    assert p["must_have_skills"] == ["Python", "web frameworks e.g. Django", "SQL"]


def test_list_ending_in_etc_does_not_swallow_the_next_sentence():
    p = _p("Data Analyst. Required skills: SQL, Excel, etc. Nice-to-have: Tableau.")
    assert p["must_have_skills"] == ["SQL", "Excel"]
    assert p["nice_to_have_skills"] == ["Tableau"]


def test_semicolons_separate_items_not_sentences():
    p = _p("Recruiter\nRequired: Sourcing; Interviewing; Onboarding")
    assert p["must_have_skills"] == ["Sourcing", "Interviewing", "Onboarding"]


@pytest.mark.parametrize(
    "sentence,years",
    [
        ("Minimum 4 years.", 4),
        ("Min. 3 years of experience.", 3),
        ("At least 5 years of experience.", 5),
        ("7+ years in backend development.", 7),
        ("3-5 years of experience in audit.", 3),
        ("Experience: 6 years of people management.", 6),
        ("الحد الأدنى 4 سنوات خبرة في هندسة البرمجيات.", 4),
        ("خبرة لا تقل عن ٣ سنوات", 3),  # Arabic-Indic digits
        ("8 سنوات من الخبرة في المحاسبة", 8),
    ],
)
def test_years_floor_phrasings(sentence, years):
    assert _p("Role\nRequired skills: Excel\n" + sentence)["min_years_experience"] == years


def test_unrelated_numbers_of_years_are_not_a_floor():
    jd = "Data Analyst\nOur company has 20 years of history.\nMinimum 3 years of experience."
    assert _p(jd)["min_years_experience"] == 3
    assert _p("Data Analyst\nFounded 20 years ago.\nRequired: SQL")["min_years_experience"] is None


def test_arabic_jd_is_parsed():
    jd = (
        "وظيفة: مهندس ذكاء اصطناعي — شركة تجريبية\n"
        "المهارات المطلوبة: Python, FastAPI, RAG, SQL, Docker\n"
        "يفضل: Kubernetes\n"
        "الحد الأدنى 4 سنوات خبرة في هندسة البرمجيات أو تعلم الآلة.\n"
        "مطلوب درجة البكالوريوس في علوم الحاسب أو تخصص ذي صلة."
    )
    p = _p(jd)
    assert p["role_title"] == "مهندس ذكاء اصطناعي — شركة تجريبية"
    assert p["must_have_skills"] == ["Python", "FastAPI", "RAG", "SQL", "Docker"]
    assert p["nice_to_have_skills"] == ["Kubernetes"]
    assert p["min_years_experience"] == 4
    assert p["_degree_level"] == "bachelor"


def test_rubric_from_the_readme_jd_has_no_fused_requirement(monkeypatch):
    monkeypatch.setenv("CV_LLM_MODE", "mock")
    from app.config import get_settings
    from app.scoring import rubric

    rub = rubric.build(
        get_settings(),
        "AI Engineer. Required skills: Python, FastAPI, RAG, Agents, SQL, Docker. Minimum 4 years.",
    )
    labels = [r["label"] for r in rub["requirements"]]
    assert labels == ["Python", "FastAPI", "RAG", "Agents", "SQL", "Docker", "4+ yrs experience"]
