# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-10-16

A hardening release. Several defaults changed to fail safe — see **Changed** before
upgrading.

### Security

- **Prompt-injection screen redesigned.** It now matches the *structure* of a
  directive aimed at the model (instruction override, role/persona hijack,
  ranking/score manipulation, fake system/role markers, hidden-text tricks) in
  English **and Arabic**, on a normalised view of the text (Unicode compatibility
  folding, zero-width / bidi / tag characters, Arabic diacritics and letter
  variants, homoglyphs, leetspeak, letter spacing). Ordinary CV lines such as
  "Worked as an AI engineer" or "Rated 5 stars by clients" are no longer flagged,
  and the Arabic instruction override that previously passed is caught. Only the
  offending sentence is neutralised; findings are graded (see **Changed**).
- **Every endpoint that receives candidate data is guarded.** The startup check
  now covers `CV_OLLAMA_URL` (which receives CV text and page images) instead of
  an unused setting, and decides locality by parsing the URL — so
  `localhost.attacker.net` or `user@evil.com` no longer count as local.
- **Authentication fails closed.** Without `CV_API_KEY` the service refuses to
  start (and answers `503` if reached anyway) instead of accepting unauthenticated
  uploads.
- **Zip-bomb limits.** Uploaded ZIPs and `.docx` files are checked against entry
  count, per-member size, total size and compression ratio from the central
  directory before extraction, and again while streaming.
- **Dependencies.** `pypdf` 6.15.0 → 6.20.1 (fixes 11 published advisories, plus
  two further hardening releases); a consistent, re-resolved lock file
  (`fastapi` 0.142.2, `uvicorn` 0.54.0, `pydantic` 2.13.5, `starlette` 1.7.0,
  `pypdfium2` 5.14.0; dev: `ruff` 0.16.10, `mypy` 2.4.0, `coverage` 7.16.2,
  `fpdf2` 2.8.9). `pip-audit` is clean again. This release also includes the
  `pypdf` 6.15.0 and `cryptography` 50.0.0 security updates, and the lock file is
  renamed to `requirements-lock.txt` so that Dependabot maintains it.
- **Container.** Runs as an unprivileged user, from a digest-pinned base image,
  installing the audited lock and shipping only the application.

### Fixed

- Two different CVs with the same file name (e.g. two `resume.pdf`) are both
  analysed; previously one overwrote the other and the survivor was listed twice.
  Byte-identical files are detected as duplicates by content hash, even without
  contact details.
- Mock JD parser: a one-line job description is split into sentences, so
  "… SQL, Docker. Minimum 4 years." yields six skills and a 4-year minimum rather
  than one unmeetable requirement; the years floor is only read from a sentence
  about experience; Arabic job descriptions are parsed too.
- Mock query engine: "shortlist the top 2" no longer treats "2" as the role, and
  "rank by years" is a plain sort on verified years.
- Nothing is written outside the repository during tests or development: the
  session store is created when the app starts, not at import time.
- Corrupt PDFs and `.docx` files degrade to a *Needs Review* candidate and are
  logged by error class.
- README badges and project URLs point to the right repository.

### Changed

- **The project is renamed Sirah** (سيرة, Arabic for the account of a life: a
  résumé). The repository moves to `Almehmadi-Ai/sirah`; the package name, the
  `service` field of `/health`, the logger name and the Docker Compose service
  are `sirah`.
- **Injection consequences are graded.** High-confidence attacks hold the
  candidate for human review (`INJECTION_SUSPECTED`, listed with the reason);
  weaker signals add a non-gating advisory (`INJECTION_NEUTRALIZED`) and the
  candidate is still scored. Nobody is removed silently.
- **Storage is memory-only by default.** `CV_DATA_DIR` defaults to empty (it was
  `/app/data`); persistence is opt-in, also in Docker Compose.
- `CV_MAX_ZIP_ENTRIES` now counts every archive entry and defaults to 1000
  (was 500 supported members).
- Uploads are streamed to disk with the size cap enforced as bytes arrive.
- `POST /sessions/{id}/audit` validates its body: `labels` and `selected` must be
  sent together and selections must be booleans (`422` otherwise).
- `/health` reports `privacy.endpoints_local` and whether remote endpoints are
  allowed; `ocr.vision_enabled` reflects the local VLM rescue.
- The documentation describes the fairness feature as adverse-impact
  (four-fifths rule) reporting that supports a bias audit, not as an audit.
- The Docker image no longer installs `fonts-dejavu-core`, which nothing in it
  used.
- Internal: orchestration moved from `app/main.py` to `app/service.py`; the
  candidate profile is typed (`app/profiles.py`); evidence verification, name
  heuristics and the injection rule table have their own modules.
- `SCORING_REV` 11 → 12: rubrics cached by the old JD parser are rebuilt.

### Added

- `CV_ALLOW_REMOTE_ENDPOINTS` (explicit, loudly-logged opt-out of the locality
  guard), `CV_DISABLE_AUTH` (development only), `CV_MAX_ZIP_TOTAL_MB`,
  `CV_MAX_ZIP_RATIO`.
- Test corpora for the injection screen (179 benign and 82 adversarial lines),
  zip-bomb, privacy-guard, authentication, storage and upload tests, and
  docs-as-tests that replay the README table and `docs/example-output.md`.
- CI runs the golden-set evaluation (with Tesseract, so the OCR checks run) and
  prints its scorecard; Dependabot watches the Docker base image.
- A configuration reference (`docs/configuration.md`), this changelog, and an
  updated `SECURITY.md`.

### Removed

- `CV_VISION_OCR_URL` and `CV_ALLOW_CLOUD_OCR`: no code ever used them. A leftover
  value is reported as ignored at startup.

## [0.1.1] - 2026-07-18

### Fixed

- Serialise all pypdfium2 access; PDFium is not thread-safe.

### Changed

- Dependency updates (Python packages, `actions/checkout`, `actions/setup-python`,
  `gitleaks-action`) and live CI / Security status badges.

## [0.1.0] - 2026-07-18

### Added

- Initial public release.

[Unreleased]: https://github.com/Almehmadi-Ai/sirah/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Almehmadi-Ai/sirah/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Almehmadi-Ai/sirah/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Almehmadi-Ai/sirah/releases/tag/v0.1.0
