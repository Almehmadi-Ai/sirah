# Security policy

## Supported versions

Only the latest release (the `main` branch) receives security fixes. See
[CHANGELOG.md](CHANGELOG.md) for what changed in each release.

## Reporting a vulnerability

Please report suspected vulnerabilities **privately** via GitHub Security
Advisories ("Report a vulnerability" on the repository's *Security* tab) rather
than opening a public issue. Include a description, reproduction steps, and the
affected version. You can expect an initial acknowledgement within a few days.

## Design posture

- **On-box by default.** Candidate PII never leaves the machine. Optional models
  run on a local Ollama; a startup guard refuses to boot if any endpoint that
  receives candidate data (today: `CV_OLLAMA_URL`) is not on this machine or the
  operator's private network. Locality is decided by parsing the URL, not by
  substring matching. The only override is the explicit
  `CV_ALLOW_REMOTE_ENDPOINTS=true`, which logs a privacy warning on every start.
- **Authentication fails closed.** Every data endpoint requires `X-API-Key`, and
  the service refuses to start without `CV_API_KEY` (keys are compared in
  constant time). `CV_DISABLE_AUTH=true` exists for local development only, is
  logged as a warning, and is ignored whenever a key is configured.
- **Ephemeral data.** Analysed profiles live in memory, in a per-session store
  with a sliding TTL, and auto-expire; `POST /sessions/{id}/reset` erases them
  immediately. Nothing is written to disk unless the operator sets `CV_DATA_DIR`
  (opt-in persistence that follows the same TTL and reset rules).
- **Untrusted-input handling.** Résumés are treated as adversarial:
  - a prompt-injection screen (English and Arabic, robust to Unicode
    obfuscation) neutralises model-directed instructions before any model sees
    them, and holds high-confidence attacks for human review;
  - uploads are streamed with a per-file size cap, and ZIP / `.docx` archives
    are checked against decompression limits (entries, per-member and total
    size, compression ratio) before extraction and again while streaming;
  - parser failures on malformed files degrade to a "needs review" candidate and
    are logged by error class — they never abort a batch;
  - protected attributes are never extracted.
- **Privacy-safe logging.** Logs carry only non-identifying metadata; it is
  structurally impossible to log CV content (see `app/logging_safe.py`).
- **Supply chain.** Direct dependencies are pinned and the full runtime set is
  locked (`requirements-lock.txt`, which the Docker image installs). CI runs
  `gitleaks` and `pip-audit`; Dependabot proposes Python, GitHub Actions and
  base-image updates.
- **Container.** The image is built from a digest-pinned base, runs as an
  unprivileged user, and contains only the application code.

## Known limitations

- The prompt-injection screen is heuristic: it reduces risk, it does not
  eliminate it. Evidence-grounded, deterministic scoring bounds what an
  undetected instruction can achieve.
- Adverse-impact (four-fifths) figures are computed from labels the operator
  supplies. They support a bias audit; they are not one.
- A private-network address passes the locality guard by design (Docker's bridge
  needs it). It is still the operator's job to keep the model host inside their
  own trust boundary.

## Scope

This is a decision-support tool and a portfolio project. All bundled data is
synthetic (see [NOTICE](NOTICE)). Deploying it against real candidate data is the
operator's responsibility; review the design posture above and
[docs/guardrails.md](docs/guardrails.md) first.
