"""Generate a diverse golden set of synthetic CVs for evaluation:
clean EN, junior, no-email, sparse, a same-name pair (different people),
a scanned (image-only) CV that forces OCR, and a multi-CV-in-one-PDF.
Each fixture ships with an `EXPECT` record for the metrics harness.

All content is fictional (see NOTICE). Addresses on the reserved example.com
domain are deliberately treated as placeholders by the extractor, so the
multi-CV fixture - whose detection keys on distinct real-looking addresses -
uses contoso.com, Microsoft's documentation domain for a fictional company.

Usage:  python tests/make_golden.py /tmp/golden
"""

import json
import os
import sys

from fpdf import FPDF


def _text_pdf(path, lines):
    pdf = FPDF(unit="pt", format="A4")
    pdf.add_page()
    y = 64
    for text, size in lines:
        if text:
            pdf.set_font("Helvetica", size=size)
            pdf.text(64, y, text)
        y += size + 6
    pdf.output(path)


def _multi_pdf(path, blocks):
    """One PDF, several people (each a page) -> should be flagged multi_cv."""
    pdf = FPDF(unit="pt", format="A4")
    for lines in blocks:
        pdf.add_page()
        y = 64
        for text, size in lines:
            if text:
                pdf.set_font("Helvetica", size=size)
                pdf.text(64, y, text)
            y += size + 6
    pdf.output(path)


def _scanned_pdf(path, lines):
    """Rasterize text to an image-only PDF (no text layer) so it MUST be OCR'd."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1000, 1400), "white")
    d = ImageDraw.Draw(img)
    try:
        base = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"  # fonts-dejavu-core
        fonts = {s: ImageFont.truetype(base, s) for s in (22, 30, 38)}
    except OSError:  # no DejaVu (e.g. Windows/macOS): Pillow's scalable default font
        fonts = {s: ImageFont.load_default(size=s) for s in (22, 30, 38)}
    y = 50
    for text, size in lines:
        f = fonts.get(38 if size >= 16 else (30 if size >= 12 else 22), fonts[22])
        d.text((50, y), text, fill="black", font=f)
        y += (size * 2) + 12
    img.save(path, "PDF", resolution=200)


CLEAN = [
    ("Ahmed Ali", 18),
    ("Senior HR Specialist", 12),
    ("Email: ahmed.ali@example.com  |  Mobile: +1 202 555 0111", 10),
    ("", 10),
    ("EXPERIENCE", 12),
    ("Senior HR Specialist, Example Telecom (2021 - present)", 10),
    ("HR Officer, Demo Foods (2018 - 2021)", 10),
    ("", 10),
    ("EDUCATION", 12),
    ("BSc Business Administration, Demo University, 2017", 10),
    ("", 10),
    ("SKILLS", 12),
    ("Recruitment, Talent Acquisition, SAP SuccessFactors, Onboarding, Payroll", 10),
]
NO_EMAIL = [
    ("Khalid Omar", 18),
    ("Recruiter", 12),
    ("Mobile: +1 202 555 0114  |  Dammam, Saudi Arabia", 10),
    ("", 10),
    ("PROFESSIONAL SUMMARY", 12),
    ("Recruiter with 5 years of experience in sourcing and full-cycle hiring across the Gulf.", 10),
    ("", 10),
    ("EXPERIENCE", 12),
    ("Recruiter, Example Foods (2019 - present)", 10),
    ("- Managed end-to-end recruitment for retail and logistics roles.", 10),
    ("Talent Sourcer, Example Staffing (2017 - 2019)", 10),
    ("", 10),
    ("EDUCATION", 12),
    ("BSc Management, Example University, 2016", 10),
    ("", 10),
    ("SKILLS", 12),
    ("Recruitment, Sourcing, Interviewing, Onboarding, Stakeholder Management", 10),
]
SPARSE = [("Min Lee", 18), ("Looking for opportunities.", 10)]
SAMENAME_A = [
    ("Mohammed Ali", 18),
    ("HR Manager", 12),
    ("Email: mohammed.ali1@example.com  |  Mobile: +1 202 555 0115", 10),
    ("", 10),
    ("EXPERIENCE", 12),
    ("HR Manager, Demo Holdings (2016 - present)", 10),
    ("", 10),
    ("SKILLS", 12),
    ("Recruitment, SAP HCM, Compensation, Payroll", 10),
]
SAMENAME_B = [
    ("Mohammed Ali", 18),
    ("Talent Acquisition Specialist", 12),
    ("Email: mohammed.ali2@example.com  |  Mobile: +1 202 555 0116", 10),
    ("", 10),
    ("EXPERIENCE", 12),
    ("TA Specialist, Demo Corp (2020 - present)", 10),
    ("", 10),
    ("SKILLS", 12),
    ("Recruitment, Sourcing, Employer Branding", 10),
]
SCANNED = [
    ("Noura Saleh", 18),
    ("HR Analyst", 12),
    ("Email: noura.saleh@example.com", 10),
    ("Mobile: +1 202 555 0117", 10),
    ("", 10),
    ("EXPERIENCE", 12),
    ("HR Analyst, Demo Analytics (2019 - present)", 10),
    ("", 10),
    ("SKILLS", 12),
    ("HR Analytics, Recruitment, Excel, SAP SuccessFactors", 10),
]
MULTI_P1 = [
    ("Fatima Noor", 18),
    ("Payroll Officer", 12),
    ("Email: fatima.noor@contoso.com", 10),
    ("Payroll Officer, Sample Co (2018 - present)", 10),
    ("Skills: Payroll, GOSI, Excel", 10),
]
MULTI_P2 = [
    ("Yousef Tariq", 18),
    ("Recruiter", 12),
    ("Email: yousef.tariq@contoso.com", 10),
    ("Recruiter, Sample Co (2020 - present)", 10),
    ("Skills: Recruitment, Sourcing", 10),
]

# expectations consumed by the eval harness
EXPECT = {
    "clean.pdf": {
        "name": "Ahmed Ali",
        "placeholder_email": "ahmed.ali@example.com",  # reserved domain: never an identity
        "phone_digits": "2025550111",
        "must_skills": ["sap successfactors", "recruitment"],
        "min_years": 5,
        "status": "OK",
        "candidates": 1,
    },
    "no_email.pdf": {
        "name": "Khalid Omar",
        "phone_digits": "2025550114",
        "status": "OK",
        "candidates": 1,
        "no_email": True,
    },
    "sparse.pdf": {"name": "Min Lee", "status": "NEEDS_REVIEW", "candidates": 1},
    "samename_a.pdf": {
        "name": "Mohammed Ali",
        "phone_digits": "2025550115",
        "candidates": 1,
    },
    "samename_b.pdf": {
        "name": "Mohammed Ali",
        "phone_digits": "2025550116",
        "candidates": 1,
    },
    "scanned.pdf": {
        "name": "Noura Saleh",
        "ocr": True,
        "must_skills": ["recruitment"],
        "candidates": 1,
    },
    "multi.pdf": {"multi_cv": True, "candidates": 1},
}


def main(outdir):
    os.makedirs(outdir, exist_ok=True)
    _text_pdf(os.path.join(outdir, "clean.pdf"), CLEAN)
    _text_pdf(os.path.join(outdir, "no_email.pdf"), NO_EMAIL)
    _text_pdf(os.path.join(outdir, "sparse.pdf"), SPARSE)
    _text_pdf(os.path.join(outdir, "samename_a.pdf"), SAMENAME_A)
    _text_pdf(os.path.join(outdir, "samename_b.pdf"), SAMENAME_B)
    _scanned_pdf(os.path.join(outdir, "scanned.pdf"), SCANNED)
    _multi_pdf(os.path.join(outdir, "multi.pdf"), [MULTI_P1, MULTI_P2])
    with open(os.path.join(outdir, "expect.json"), "w", encoding="utf-8") as f:
        json.dump(EXPECT, f, ensure_ascii=False, indent=2)
    print(outdir)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/golden")
