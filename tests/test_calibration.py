"""Judge calibration: Cohen's kappa, gold-set lookup and the drift alert."""

from app.scoring import calibration


def test_cohens_kappa_perfect_and_chance():
    perfect = [("met", "met"), ("missing", "missing"), ("partial", "partial")]
    assert calibration.cohens_kappa(perfect) == 1.0
    disagree = [("met", "missing"), ("missing", "met")]
    assert calibration.cohens_kappa(disagree) < 0.5


def test_calibration_find_req_by_rtype_and_keyword():
    rub = {
        "requirements": [
            {
                "id": "r1",
                "rtype": "education",
                "label": "Bachelor degree",
                "keywords": ["CS"],
                "text": "bachelor",
            },
            {
                "id": "r2",
                "rtype": "skill",
                "label": "Java",
                "keywords": ["Java", "Python"],
                "text": "programming",
            },
        ]
    }
    assert calibration._find_req(rub, [], "education") == "r1"
    assert calibration._find_req(rub, ["Python"], "skill") == "r2"
    assert calibration._find_req(rub, ["Rust"], "skill") is None


def test_drift_alert_on_accuracy_drop(tmp_path):
    class S:
        data_dir = str(tmp_path)

    # baseline high, then a big drop -> drift alert
    calibration.record_and_check_drift(S(), {"accuracy": 1.0, "kappa": 1.0, "matcher": "judge"})
    d = calibration.record_and_check_drift(S(), {"accuracy": 0.5, "kappa": 0.4, "matcher": "judge"})
    assert d["drift_alert"] is True and d["divergence"] >= 0.25
    d2 = calibration.record_and_check_drift(
        S(), {"accuracy": 0.95, "kappa": 0.9, "matcher": "judge"}
    )
    assert d2["drift_alert"] is False
