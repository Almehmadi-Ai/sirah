"""Typed shape of a candidate profile — the system of record a batch produces.

Profiles stay plain dicts at runtime (they are persisted as JSON and travel
through the scoring and query planes unchanged); these ``TypedDict`` declarations
make the shape explicit and let mypy check every producer and consumer.
``pipeline/extract.py`` builds them; ``pipeline/dedup.py`` adds the
duplicate/conflict fields; nothing else writes to a profile.

Keys starting with ``_`` are private: they carry source text for evidence
grounding and are never surfaced to a user.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict


class Evidence(TypedDict):
    page: int
    quote: str


class NameField(TypedDict):
    value: str
    evidence: Evidence | None


class Identity(TypedDict):
    full_name: NameField
    emails: list[str]
    phones: list[str]
    links: list[str]


class Source(TypedDict):
    file: str | None  # the uploaded (or archive member) file name, for display
    page_range: list[int] | None
    container: str | None  # the ZIP it came from, if any
    sha256: NotRequired[str]  # content hash of the source document


class YearsOfExperience(TypedDict):
    value: float | None
    basis: str  # how the value was obtained ("computed from 2 dated role(s)", ...)
    note: NotRequired[str]  # stated-vs-computed contradiction, surfaced for review


class Experience(TypedDict):
    title: str
    organization: str
    start: str
    end: str
    evidence: Evidence | None


class Education(TypedDict):
    degree: str
    field: str
    institution: str
    year: str
    evidence: Evidence | None


class Skill(TypedDict):
    name: str
    source: str  # "stated" (literally in the CV) | "inferred"
    evidence: Evidence | None


class Certification(TypedDict):
    name: str
    evidence: Evidence | None


class Language(TypedDict):
    name: str
    level: str
    evidence: Evidence | None


class InjectionSummary(TypedDict):
    severity: str  # "none" | "medium" | "high"
    categories: list[str]


class Extraction(TypedDict):
    ocr_used: bool
    vlm_used: bool
    mean_ocr_confidence: float | None
    field_coverage: float
    status: str  # "OK" | "NEEDS_REVIEW"
    reasons: list[str]  # gating: any reason means NEEDS_REVIEW
    advisories: NotRequired[list[str]]  # non-gating, shown to the reviewer
    injection: NotRequired[InjectionSummary]


class PageText(TypedDict):
    page: int
    text: str
    source: str | None  # "text" | "ocr" | "vlm"
    ocr_confidence: float | None


class CandidateProfile(TypedDict):
    candidate_id: str
    display_no: int
    source: Source
    identity: Identity
    headline: str
    summary: NotRequired[str]
    total_years_experience: YearsOfExperience
    experiences: list[Experience]
    education: list[Education]
    skills: list[Skill]
    certifications: list[Certification]
    languages: list[Language]
    extraction: Extraction
    duplicate_of: str | None
    conflict_with: NotRequired[str]  # set by dedup: same identity, different content
    verification_notes: NotRequired[list[str]]  # set by dedup on the kept profile
    _excluded_by_policy: list[str]
    _pages: list[PageText]
