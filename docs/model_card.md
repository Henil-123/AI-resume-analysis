# Model Card — AI Resume Screening System

## Intended use
Decision *support* for recruiters: rank candidates, surface evidence for each
requirement, flag gaps. A human makes every hiring decision.

## Out-of-scope use
Automated rejection; interview-probability prediction; background verification;
any use without human review. Do not optimize solely against historical hiring
decisions (they may encode bias).

## Models and versions
- Semantics (cascade, loaded once, reused, cached): `BAAI/bge-small-en-v1.5`
  -> fallback `all-MiniLM-L6-v2` (384-dim) -> fallback `tfidf-cosine (light)`.
  Production default (`SEMANTIC_ENGINE=tfidf`) is the dependency-free cosine
  engine for free-tier RAM. `/health` and `/api/v1/health` report the active
  `embed_model` (cached-only read, never triggers downloads) and
  `embedding_dimension`.
- NLP: spaCy `en_core_web_sm` (E050-proof loader with blank-pipeline
  fallback); PhraseMatcher taxonomy over `data/skills/skills_dataset.json`
  plus `backend/app/data/skill_aliases.json`.
- No LLM in the scoring path. Optional LLM adapters are out of scope for
  this release; uploaded text is treated as untrusted data and never
  executed (prompt-injection-like content is flagged in the audit trail).

## Evaluation data
No human-labelled JD-resume evaluation set has been collected yet: **the
system has not been empirically validated.** Precision/recall/F1, ranking
quality, and reviewer agreement are unmeasured. See README for the planned
evaluation workflow (separate train/validation/test, no leakage).

## Known failure modes
- Image-only PDFs extract no text -> explicit `image-only-pdf` + OCR hint,
  never a silent low score presented as confident.
- Taxonomy misses novel skills -> they fall to semantic/typo recovery at
  partial credit;ADMINS extend `skill_aliases.json` without code changes.
- TF-IDF engine underperforms BERT on paraphrase; reported per requirement
  via raw cosine so reviewers can see weak semantic signals.
- Date-range experience inference is heuristic (fixed current-year anchor).

## Bias considerations
Taxonomy and weights reflect the authors' product judgment, not fitted
outcomes. Skill rarity (pool IDF) applies only to the legacy precision
score, never to requirement weights. Monitor shortlists for disparate impact
before production hiring use.

## Privacy
Uploads are validated (type/size/MIME), stored under random names, temp JD
files removed after parsing. No secrets in frontend code. Avoid sending
resumes to external services; retention is configurable (delete per
candidate or block `/clear` in production, already enforced).
