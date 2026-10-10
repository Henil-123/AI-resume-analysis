# Scoring Methodology (explainable-v2, config v2.0.0)

Single source of truth: `backend/app/config/scoring_config.yaml` (validated at
startup; `config_version` stored with every analysis for reproducibility).

## Requirement-level score

For each JD requirement `i`, all components normalized to [0, 1]:

```
R_i = 100 * (0.35*K_i + 0.35*M_i + 0.30*P_i)
```

- **K_i (keyword)**: 1.0 explicit/alias match · 0.7 fuzzy variant ·
  0.5 related concept / raw-text typo recovery · 0.0 no valid match.
  Matching is canonical + word-boundary phrase based, never naive substring
  (`go` does not match `django`; `c` does not match `c++`).
- **M_i (semantic)**: `M = max(0, cosine(u, v))` (`clip01` transform) where `u`
  is the requirement embedding and `v` the best resume-evidence embedding.
  Both raw cosine and normalized M are reported per requirement.
- **P_i (evidence)**: 1.0 demonstrated implementation · 0.7 specific practical
  use · 0.3 mentioned only · 0.0 no evidence. Planned/aspirational work caps
  at 0.3; negated mentions score 0.0; duplicate passages never inflate
  (best non-duplicate evidence decides).

Status per requirement: `matched` (R>=70) · `partial` (40-69) ·
`missing` (<40) · `uncertain` (model signal unavailable).

## Overall score (PRIMARY)

```
K = 100 * sum(w_i*K_i)/sum(w_i)      weighted keyword coverage
M = 100 * sum(w_i*M_i)/sum(w_i)      aggregate semantic relevance
E = experience-fit score 0-100
S = 0.60*K + 0.30*M + 0.10*E
```

Secondary diagnostic: `RequirementWeightedScore = sum(w_i*R_i)/sum(w_i)`.
Also reported: unweighted skill coverage. The primary score is always `S`;
the UI shows the diagnostic alongside it, never silently averaged.

Importance weights `w_i` (1 low - 5 critical): explicit required-section
placement -> 5; preferred section -> 2; frequency emphasis otherwise
(3+ mentions -> 4, 2 -> 3, else 3). Inferred weights are labeled inferred
with a reason. Equivalent requirements are deduplicated before scoring.

## Experience fit

`actual >= required -> 100`; `actual <= 0 with requirement -> 0`;
else `actual/required*100`. No JD bar: 100 if any experience found else 60.
Trainee-friendly: professional years are never demanded unless the JD states
them; academic/project work counts via evidence, not double-counted.

## Eligibility (separate from score)

`VERIFIED_ELIGIBLE` / `VERIFIED_INELIGIBLE` / `UNKNOWN`. Missing information
is never a verified failure. Formal INELIGIBLE only when the JD states an
explicit verifiable bar (degree, explicitly-mandatory skill) that the resume
contradicts. The score stays visible when eligibility is unknown.

## Missing-data policy

- No scoreable requirements -> `insufficient-data` (never divide by zero).
- Embedding model unavailable -> semantic skipped with a recorded error;
  normalized M = 0.0 for affected requirements, never fabricated.
- Empty/unreadable resumes are rejected (HTTP 400/422), never scored as 0.

## Limitations

Heuristic, uncalibrated weights (defaults chosen by product reasoning, not
fitted to labelled outcomes). Cosine similarity is not proficiency. The
system has not yet been empirically validated — see `model_card.md`.
Do not describe scores as interview-selection probabilities.
