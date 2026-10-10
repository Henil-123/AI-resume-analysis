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
  **Raw cosine and normalized M are stored separately**: `raw_cosine` (a
  similarity in [-1,1], explicitly labelled "not a probability") and
  `semantic_score` (the [0,1] value used in the formula). A qualitative
  `semantic_band` (strong/moderate/weak/minimal, thresholds from
  `scoring_config.yaml`) accompanies them, because a cosine is not a
  proficiency percentage.
  A requirement is compared **only against evidence passages that actually
  mention that skill**, selecting the best valid match across up to 16
  candidate passages. If the resume contains no such passage, `M_i = 0.0`
  with `semantic_basis: "no-evidence"` — never a similarity borrowed from
  unrelated sentences in the resume.
  Semantic scores are never inflated to raise the headline.

### Embedding provenance

Every requirement row records `semantic_model_meta` (engine kind, model id,
library version, embedding dimension) and `semantic_query_text` (the exact
requirement text that was embedded), alongside `semantic_evidence_ids`. A
semantic number can therefore be attributed to a specific model and input.

### Calibration status (measured, not assumed)

`services/semantic_calibration.py` holds a small hand-written seed set of
`(requirement, passage, human label)` triples across `match` / `related` /
`unrelated`. `calibration_report()` scores it and reports
`ordering_match_ge_related_ge_unrelated`. The result is surfaced in
`technical_audit.semantic_calibration`.

**Measured results** (12 cases, group mean of normalized cosine):

| engine | match | related | unrelated | ordering holds |
|---|---|---|---|---|
| `BAAI/bge-small-en-v1.5` | 0.7675 | 0.6558 | 0.5018 | yes |
| `tfidf-cosine (light)` | 0.4310 | 0.0616 | 0.1929 | **no** |

The light TF-IDF engine is lexical and does **not** separate `related` from
`unrelated` reliably (an unrelated passage can outscore a related one). The
report states this per analysis rather than asserting a pass.

This seed set is a smoke test of band ordering and a regression guard, **not** a
statistically validated calibration; the thresholds in `scoring_config.yaml`
remain defaults. A larger human-labelled corpus is the outstanding work.
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

### Professional vs project experience

Experience is extracted by `services/experience_extractor.py` from **section
context**, never from a text-wide date scan. Only sections recognised as
employment (Work Experience / Internship Experience / Employment) contribute
professional years. Project and education periods are extracted separately and
reported as `project_years` and `education_years`.

This distinction matters: a resume listing `2021 - 2025` for a degree and
`2024 - 2025` for a project previously produced "4 years of professional
experience" for a candidate whose only employment was a two-month internship.

Rules:
- Date ranges are merged as **month intervals**, so overlapping or nested
  roles are never double counted.
- Month precision is honoured ("June 2025 - August 2025" = 2 months).
- A start date with no end date and no "present/current" marker is reported
  in `unverifiable` — the end date is **never invented**.
- Without a recognisable employment section, bare date ranges are NOT
  attributed to employment (they may be a degree or a personal project);
  only explicit statements ("3+ years of experience", "2 months") are used.
- When nothing can be verified the result is an explicit **unknown**
  (`confidence: "none"`), never a claim of zero experience.

## Report surfaces

The report exposes distinct sections and never recomputes a score:

| surface | endpoint | source |
|---|---|---|
| JSON report | `GET /api/v1/analyses/<id>/report?format=json` | stored `report` |
| PDF report | `GET /api/v1/analyses/<id>/report?format=pdf` | the same stored `report` |
| requirements | `GET /api/v1/analyses/<id>/requirements` | stored `requirement_scores` |

The PDF is rendered from the same stored report object
(`services/pdf_report.py`), so it cannot disagree with the headline, the
breakdown, the JSON export or the API. It covers the primary score, each
component, the diagnostic requirement-weighted score, eligibility with
per-criterion sources, verified professional vs project experience,
direct/transferable/skill-list-only skills, missing and unverified skills,
source-grounded recommendations, requirement-level detail with evidence
sections and pages, and model/config metadata.

## Eligibility

Eligibility is evaluated strictly separately from compatibility scoring.

`VERIFIED_INELIGIBLE` requires an explicit, source-backed JD rule (degree
minimum, graduation batch, active backlogs) that is demonstrably violated.
**A missing skill is never an eligibility failure** — it is recorded in
`skill_gaps` and affects only the score. Every criterion carries `detail`,
`source` and `rule`. Absent information yields `UNKNOWN`.

If the JD states no rule at all, the result is `UNKNOWN`; no rule (such as a
Diploma/Certificate bar) is ever invented.

## Precision and rounding

Components and the primary score are computed at **full precision**.
`formula_inputs` stores the exact `K`, `M`, `E` and `overall_exact`, so

    w_keyword*K + w_semantic*M + w_experience*E == overall_exact

holds to floating-point precision. Rounding to 1 decimal place happens only in
the presentation layer (`keyword_coverage`, `overall_score`, ...). The headline
cannot disagree with the formula applied to the published inputs.

## Authoritative score (single source of truth)

`aggregate()` computes the score once. `final_score`, the `score_breakdown`,
the requirement table, the API payload, the report and the Excel export all
read those same stored values — no component recomputes it independently.
The legacy 45/30/15/10 blend (with must-have gate) is still computed and
exposed as `legacy_final_score` for comparison, but it is never headlined.

`requirement_weighted_score` is a **diagnostic only** and is labelled as such;
it is never the headline.

Each result records `formula_version`, `config_version` and `formula_inputs`
(`K`, `M`, `E`, `overall_exact`, weights, requirement count, total weight),
plus per-requirement `formula_inputs` (`K`, `M`, `P` and the three weights),
so every published number can be re-derived from the published inputs.

## Requirement classification

Every requirement carries a `classification`:

| classification | mandatory? | effect |
|---|---|---|
| `explicit_mandatory` | yes | listed under a detected required/must-have section, or with mandatory wording |
| `required_learning_outcome` | no | awareness-level ("exposure to", "familiarity with") |
| `preferred` | no | listed under preferred/nice-to-have |
| `behavioural_competency` | no | communication, teamwork, leadership |
| `inferred_priority` | no | inferred from mention frequency only |

`weight_source` records whether the importance weight was **explicit** in the
JD or **inferred**. An inferred requirement is never described as explicitly
mandatory. Equivalent skills are collapsed into one requirement so a single
capability is not double counted.

Section detection stops at breaker headers (education, eligibility, a new role
block), so a second role's skills in the same JD (e.g. a `.NET` track under a
Java role) are never attributed to the selected role.

### Manual review

Extraction is heuristic, so `PATCH /api/v1/analyses/<id>/requirements` lets a
reviewer correct the requirement set **before** the score is finalised:

    {"exclude": ["asp.net"], "restore": ["kubernetes"],
     "weights": {"java": 4}, "mandatory": {"react": true},
     "reviewed_by": "Alice", "reason": "no .NET curriculum for this role"}

The analysis is re-scored from the same source documents and the stored
requirement set. Every override is recorded on the requirement
(`reviewed_by`, `review_reason`, `weight_source: "reviewed"`), so a corrected
score stays traceable. Unknown requirements, out-of-range weights and
non-numeric weights are rejected with the offending value named.

## Domain matches

A concrete technology satisfies a **generic** requirement (`mysql` satisfies
`SQL`), credited at the fuzzy level with an explicit note. The relation is
one-way: `sql` does **not** satisfy `mysql`. Implemented in
`skill_aliases.domain_covers`; deliberately not an alias map, because collapsing
`mysql` -> `sql` would let a MySQL resume satisfy a PostgreSQL requirement.

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
