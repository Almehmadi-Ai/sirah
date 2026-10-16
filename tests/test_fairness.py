"""Fairness reporting: protected-attribute leakage checks and four-fifths-rule
adverse-impact ratios."""

from app import fairness


def _prof(no, extra=None):
    p = {
        "candidate_id": f"c_{no}",
        "display_no": no,
        "identity": {"full_name": {"value": f"Person {no}"}},
        "skills": [{"name": "Python"}],
        "_excluded_by_policy": ["gender", "age"],
    }
    if extra:
        p.update(extra)
    return p


def test_fairness_clean_profiles_no_leak():
    rep = fairness.leakage_report([_prof(1), _prof(2)], texts=["Bachelor degree, 5 years"])
    assert rep["clean"] and rep["protected_leaks"] == 0


def test_fairness_detects_profile_leak():
    rep = fairness.leakage_report([_prof(1, {"gender": "female"})])
    assert not rep["clean"] and rep["protected_leaks"] >= 1
    assert rep["details"][0]["field"] == "gender"


def test_fairness_detects_output_leak():
    rep = fairness.leakage_report([_prof(1)], texts=["Name: X, Gender: Female, Nationality: Foo"])
    tags = {d.get("attribute") for d in rep["details"]}
    assert "gender" in tags and "nationality" in tags


def test_four_fifths_flags_adverse_impact():
    # 80% of men selected, 40% of women -> ratio 0.5 < 0.8 -> adverse
    selected = {
        "m1": True,
        "m2": True,
        "m3": True,
        "m4": True,
        "m5": False,
        "w1": True,
        "w2": True,
        "w3": False,
        "w4": False,
        "w5": False,
    }
    groups = {
        **{f"m{i}": {"gender": "male"} for i in range(1, 6)},
        **{f"w{i}": {"gender": "female"} for i in range(1, 6)},
    }
    r = fairness.four_fifths_audit(selected, groups)
    assert r["adverse_impact"] is True
    assert r["axes"]["gender"]["groups"]["female"]["adverse"] is True


def test_four_fifths_passes_when_balanced():
    selected = {
        "m1": True,
        "m2": True,
        "m3": False,
        "m4": True,
        "w1": True,
        "w2": True,
        "w3": False,
        "w4": True,
    }
    groups = {
        **{f"m{i}": {"gender": "male"} for i in range(1, 5)},
        **{f"w{i}": {"gender": "female"} for i in range(1, 5)},
    }
    r = fairness.four_fifths_audit(selected, groups)
    assert r["adverse_impact"] is False
