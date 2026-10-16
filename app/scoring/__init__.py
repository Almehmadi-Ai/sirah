"""Deterministic fit-scoring plane.

Pipeline: JD text -> rubric (LLM proposes, code validates) -> per-requirement
matching (deterministic term evidence + local embeddings; NO per-candidate LLM)
-> deterministic aggregation (weights, gates, caps, bands) -> deterministic
ranking -> code-rendered report.

Design rule: semantic components only PROPOSE (rubric structure, similarity);
deterministic code DECIDES, VERIFIES, COMPUTES and RENDERS. Same session +
same JD => byte-identical report.
"""

# Bump whenever scoring or rubric-building logic changes: it namespaces the
# per-session rubric/score caches, so persisted sessions never serve results
# computed by an older scoring plane.
SCORING_REV = "12"
