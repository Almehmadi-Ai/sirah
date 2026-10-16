"""Deterministic anchors: contact details and years-of-experience arithmetic."""

import datetime as dt

from app.pipeline import anchors

THIS_YEAR = dt.date.today().year


def test_find_emails():
    assert anchors.find_emails("contact ahmed.ali@contoso.com now") == ["ahmed.ali@contoso.com"]
    assert anchors.find_emails("none here") == []


def test_find_phones_international():
    phones = anchors.find_phones("Mobile: +1 202 555 0123")
    assert phones and "+12025550123" in phones[0].replace(" ", "")


def test_phone_does_not_match_year_range():
    # a date range must never be read as a phone number
    assert anchors.find_phones("Worked 2018-2023 at the company") == []


def test_arabic_indic_digits_normalize():
    assert anchors.normalize_digits("٠٥٠١٢٣") == "050123"


def test_placeholder_emails_filtered():
    txt = (
        "Contact: resumesample@example.com, your.email@gmail.com, "
        "real.person@contoso.com, firstname.lastname@corp.test, anna@fabrikam.com"
    )
    emails = anchors.find_emails(txt)
    assert "real.person@contoso.com" in emails
    assert "anna@fabrikam.com" in emails  # real-looking domains stay
    assert "resumesample@example.com" not in emails  # RFC-2606 domain
    assert "your.email@gmail.com" not in emails  # placeholder local-part
    assert "firstname.lastname@corp.test" not in emails


def test_compute_total_years_merges_overlap():
    exps = [{"start": "2018", "end": "2021"}, {"start": "2021", "end": "present"}]
    years, n = anchors.compute_total_years(exps)
    assert n == 2 and years is not None and years >= 5


def test_month_boundary_role_change_is_contiguous():
    ty, n = anchors.compute_total_years(
        [
            {"start": "June 2015", "end": "December 2018"},
            {"start": "January 2019", "end": "Present"},
        ]
    )
    assert n == 2
    expected = ((THIS_YEAR * 12 + dt.date.today().month) - (2015 * 12 + 6) + 1) / 12.0
    assert abs(ty - round(expected, 1)) < 0.15  # not 10: Dec 2018 -> Jan 2019 is contiguous


def test_year_only_ranges():
    ty, _ = anchors.compute_total_years([{"start": "2014", "end": "2019"}])
    assert ty == 5.0
    ty2, _ = anchors.compute_total_years(
        [{"start": "2010", "end": "2014"}, {"start": "2018", "end": "2022"}]
    )
    assert ty2 == 8.0


def test_month_overlap_union():
    ty, _ = anchors.compute_total_years(
        [
            {"start": "March 2022", "end": "Present"},
            {"start": "September 2021", "end": "February 2022"},
        ]
    )
    expected = ((THIS_YEAR * 12 + dt.date.today().month) - (2021 * 12 + 9) + 1) / 12.0
    assert abs(ty - round(expected, 1)) < 0.15  # ~4.9 — below a 5-year floor


def test_stated_years_claim():
    assert anchors.stated_years_claim("Senior QA Engineer with 8 years of experience") == 8.0
    assert anchors.stated_years_claim("no numbers here") is None
