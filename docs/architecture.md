# Architecture

```
Browser (vanilla JS, no build) ──REST──> Flask (backend/app.py)
   5 screens: Analyze/Rankings/          ├─ resume_parser (per-page text, MIME/size guards)
   Dashboard/Shortlist/Compare           ├─ section_parser (resume + JD sections)
   + explainable detail drawer           ├─ skill_extractor (spaCy PhraseMatcher + aliases)
     (requirement table, evidence,       ├─ requirement_extractor (structured JD requirements)
      eligibility, recalculate)          ├─ evidence_extractor (passage records + polarity)
                                         ├─ scoring_engine (K/M/P, R_i, S, ReqWeighted)
                                         ├─ eligibility_checker (ELIGIBLE/INELIGIBLE/UNKNOWN)
                                         ├─ report_generator (8-section report + gaps)
                                         └─ db_handler (MongoDB, JSON fallback)
```

## Pipeline (12 stages)
1. Ingest: multipart resume + JD file/text; extension/MIME/size checks.
2. Extract: per-page text with page map; empty/image-only -> 400/422 + hint.
3. Clean: lowercase, preserve `C++`/`C#`/`.NET`, keep raw for evidence.
4. Sections: configurable header patterns; fallback `other`.
5. Requirements: canonical skills, 1-5 weights, mandatory + source,
   dedupe equivalents, source-text refs.
6. Evidence: per-passage records (section/page/type/polarity/planned/dup).
7. Normalize: `skill_aliases.json`; related-but-different techs never merged.
8. Features: K_i deterministic; M_i real embedding inference (cached model,
   batched, per-requirement best passage); P_i rubric.
9-10. Score: R_i blend, then primary S + diagnostic ReqWeighted.
11. Explain: requirement table, evidence quotes, gaps, recommendations.
12. Report: interactive UI + `GET .../report` JSON download.

## Key decisions
- Additive explainable layer: legacy precision score and all existing element
  IDs/JS contracts unchanged; `explainable` attached to `/analyze` payloads.
- Versioned API `/api/v1/*` alongside legacy routes.
- Model loaded once (`job_matcher._get_embed_model`), embedding cache (4000),
  JD artifacts cached (64). TF-IDF engine on low-RAM hosts via env.
- Persistence: MongoDB primary, local JSON fallback (PostgreSQL/SQLAlchemy
  not adopted — zero-setup fallback better fits this deployment; documented
  alternative). Pydantic not adopted — stdlib validation avoids new runtime
  deps on free-tier images. scikit-learn not required — cosine via NumPy
  (mathematically identical for normalized vectors).
