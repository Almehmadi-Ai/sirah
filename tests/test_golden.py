"""Golden-set guarantees, run on every CI build in mock mode (no model):
100% evidence-validity, zero protected leakage, and every golden check
(same-name no-merge, multi-CV flag, identity, dedup, and OCR of a scanned CV).

The OCR checks need the Tesseract binary with English and Arabic language data.
CI installs it and sets CV_REQUIRE_OCR=1, so there they can never be skipped;
on a machine without Tesseract they are skipped with that reason. Set
CV_LIVE_LLM=1 (with CV_LLM_MODE=ollama) to grade a real local model instead."""

import os

import pytest

from app.config import get_settings
from tests import eval_golden


@pytest.fixture(scope="module")
def metrics(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    if os.environ.get("CV_LIVE_LLM") != "1":
        mp.setenv("CV_LLM_MODE", "mock")
    mp.setenv("CV_DATA_DIR", "")
    try:
        return eval_golden.evaluate(get_settings(), str(tmp_path_factory.mktemp("golden")))
    finally:
        mp.undo()


def test_every_displayed_quote_is_real(metrics):
    assert metrics["evidence_validity"] == 1.0, f"{metrics['evidence_validity']:.2%}"
    assert metrics["evidence_quotes"] > 0


def test_no_protected_attribute_leaks(metrics):
    assert metrics["protected_leaks"] == 0


def test_golden_checks(metrics):
    failed = [name for name, ok, needs_ocr in metrics["details"] if not ok and not needs_ocr]
    assert not failed, f"failed checks: {failed}"


def test_golden_ocr_checks(metrics):
    if not eval_golden.ocr_ready():
        reason = "Tesseract with eng+ara language data is not installed (CI installs it)"
        if os.environ.get("CV_REQUIRE_OCR") == "1":
            pytest.fail(reason)
        pytest.skip(reason)
    ocr_checks = [(name, ok) for name, ok, needs_ocr in metrics["details"] if needs_ocr]
    assert ocr_checks, "the golden set lost its scanned fixture"
    failed = [name for name, ok in ocr_checks if not ok]
    assert not failed, f"failed OCR checks: {failed}"
