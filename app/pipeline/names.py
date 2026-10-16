"""Person-name heuristics: reject headings, job titles and template placeholders
that a model or a layout scan may offer as a candidate's name, and normalise the
case of the names that pass.
"""

from __future__ import annotations

import re
from typing import Any

# Section headings / labels that must never be accepted as a person's name.
NAME_STOPWORDS = {
    "work experience",
    "experience",
    "education",
    "skills",
    "contact",
    "profile",
    "summary",
    "professional summary",
    "references",
    "projects",
    "certifications",
    "certification",
    "languages",
    "about me",
    "about",
    "personal information",
    "personal info",
    "work history",
    "employment",
    "employment history",
    "objective",
    "career objective",
    "expertise",
    "achievements",
    "interests",
    "curriculum vitae",
    "resume",
    "cv",
    "portfolio",
    "details",
    "contact me",
    "my contact",
    # universal template placeholders (never a real person's name)
    "full name",
    "your name",
    "your name here",
    "name here",
    "insert name",
    "name surname",
    "first last",
    "first name last name",
    "firstname lastname",
    "candidate name",
    "applicant name",
    "lorem ipsum",
    "john doe",
    "jane doe",
}


JOB_TITLE_WORDS = {
    "designer",
    "engineer",
    "manager",
    "developer",
    "analyst",
    "executive",
    "officer",
    "specialist",
    "coordinator",
    "consultant",
    "director",
    "intern",
    "assistant",
    "representative",
    "administrator",
    "technician",
    "supervisor",
    "architect",
    "scientist",
    "accountant",
    "recruiter",
    "ux",
    "ui",
    "hr",
    "salesperson",
    "marketer",
    "strategist",
    "designerr",
    "freelancer",
    "photographer",
    "attendant",
    "nurse",
    "teacher",
    "driver",
    "cashier",
    "clerk",
    "agent",
    "stylist",
    "chef",
    "pilot",
    "waiter",
    "barista",
    "receptionist",
    "operator",
    "associate",
    "lead",
    "head",
    "trainee",
    "apprentice",
    "writer",
    "editor",
}


# Single words that mark a line as a heading, a degree, an institution or a
# company rather than a person (matched per word, plural-tolerant).
_HEADING_TOKENS = {
    "experience",
    "education",
    "skills",
    "profile",
    "summary",
    "references",
    "projects",
    "professional",
    "qualifications",
    "objective",
    "achievements",
    "expertise",
    "contact",
    "curriculum",
    "vitae",
    "resume",
    "cv",
    "various",
    "industries",
    "work",
    "history",
    "personal",
    "details",
    "info",
    "information",
    "portfolio",
    # placeholder-person vocabulary (template artifacts, never real names)
    "candidate",
    "applicant",
    "jobseeker",
    # degree / education words (template CVs sometimes put a degree where a name goes)
    "bachelor",
    "master",
    "diploma",
    "degree",
    "bsc",
    "msc",
    "mba",
    "phd",
    "doctorate",
    "university",
    "college",
    "institute",
    "school",
    "academy",
    "laude",  # magna/summa cum laude honors lines
    # company-name suffixes (company put where a name goes)
    "inc",
    "ltd",
    "llc",
    "corp",
    "corporation",
    "airlines",
    "solutions",
    "technologies",
    "studio",
    "studios",
    "company",
    "group",
    "agency",
    "enterprises",
    "services",
    "holdings",
    "labs",
    "systems",
}


def normalize_case(name: str) -> str:
    """Present names in consistent Title Case so a batch never shows the same
    person as 'JESSICA CLAIRE', 'Jessica CLAIRE' and 'Jessica Claire' side by
    side (scanned CVs print names in varied case). Only re-cases a WORD that is
    entirely upper- or entirely lower-case; mixed-case words (McDonald, O'Brien,
    van der Berg, DeSilva) are left exactly as written."""
    if not name:
        return name
    out = []
    for w in name.split():
        core = w.strip("().,")
        if core and (core.isupper() or core.islower()) and any(c.isalpha() for c in core):
            # title-case each hyphen/apostrophe segment: al-otaibi -> Al-Otaibi
            w = re.sub(r"[A-Za-z]+", lambda m: m.group(0).capitalize(), w)
        out.append(w)
    return " ".join(out)


def is_valid_name(name: str) -> bool:
    """False for anything that is not plausibly a person's name."""
    n = re.sub(r"\s+", " ", (name or "").strip())
    if not n or len(n) < 3 or len(n) > 60:
        return False
    low_full = n.lower()
    if low_full in NAME_STOPWORDS:
        return False
    # URLs / emails / markup / digits / heading punctuation are never part of a
    # real name (OCR garbage, template placeholders, section headings with a
    # trailing colon like "Technical Skills:").
    if any(c in n for c in "@{}|/\\<>()[]#:;•·*_=+~") or any(ch.isdigit() for ch in n):
        return False
    if any(tok in low_full for tok in ("www", ".com", ".net", ".org", "http", "://")):
        return False
    words = n.split()
    if len(words) > 5:
        return False
    # letter-spacing / OCR fragmentation: too many 1-char tokens ("D A N I", "e ,")
    singles = sum(1 for w in words if len(re.sub(r"[^A-Za-z؀-ۿ]", "", w)) <= 1)
    if singles >= 2 or (len(words) >= 2 and singles >= 1 and len(words) <= 2):
        return False
    # needs enough real alphabetic content
    if sum(1 for ch in n if ch.isalpha() or "؀" <= ch <= "ۿ") < 3:
        return False
    # a Latin-script name has at least one Uppercase-initial word; an all-
    # lowercase string is a sentence fragment ("specific requirements."), not
    # a person. Arabic script (no case) is exempt.
    if not any(w[:1].isupper() or ("؀" <= w[:1] <= "ۿ") for w in words if w):
        return False
    low = set()
    for w in words:
        t = w.lower().strip(".,:;|-•·")
        low.add(t)
        # plural-tolerant heading detection ("RESUMES" -> "resume",
        # "HISTORIES" -> "history")
        if t.endswith("ies") and t[:-3] + "y" in _HEADING_TOKENS:
            low.add(t[:-3] + "y")
        elif t.endswith("s") and t[:-1] in _HEADING_TOKENS:
            low.add(t[:-1])
    if len(words) == 1 and words[0].lower() in NAME_STOPWORDS:
        return False
    if low & JOB_TITLE_WORDS:  # job-titles-as-names
        return False
    if low & _HEADING_TOKENS:  # section headings / fillers
        return False
    return True


def heuristic_name(pages: list[dict[str, Any]]) -> str:
    """First name-like line near the top: 2-4 wordish tokens, no digits/@, not a
    section heading. Scans several lines (design CVs often put a heading first)."""
    if not pages:
        return ""
    lines = [ln.strip() for ln in (pages[0].get("text") or "").splitlines() if ln.strip()]
    for s in lines[:8]:
        if "@" in s or any(c.isdigit() for c in s):
            continue
        if s.lower() in NAME_STOPWORDS:
            continue
        words = s.split()
        if 1 < len(words) <= 4 and len(s) <= 50 and is_valid_name(s):
            return s
    return ""
