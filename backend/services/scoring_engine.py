"""Explainable scoring engine (spec sections 8-13).

Formulas (defaults from scoring_config.yaml, all configurable):
  K_i in {1.0 exact/alias, 0.7 fuzzy, 0.5 partial/typo, 0.0 missing}
  K = 100 * sum(w_i*K_i)/sum(w_i)            (weighted keyword coverage)
  M_i = transform(cosine(u,v)) in [0,1]      (raw + normalized both reported)
  M = 100 * mean(M_i)                        (aggregate semantic relevance)
  P_i in {1.0, 0.7, 0.3, 0.0}                (evidence strength rubric)
  R_i = 100 * (0.35*K_i + 0.35*M_i + 0.30*P_i) (requirement-level score)
  S = 0.60*K + 0.30*M + 0.10*E               (PRIMARY overall score)
  ReqWeighted = sum(w_i*R_i)/sum(w_i)        (secondary diagnostic)

Zero total weight -> {"status": "insufficient-data"} (never divide by zero).
No model -> semantic skipped, never fabricated (status records the gap).
"""
import os
import time

try:
    from services import job_matcher as _jm
    from services.skill_aliases import normalize_skill, domain_covers
except ImportError:
    import job_matcher as _jm
    from skill_aliases import normalize_skill, domain_covers

import yaml as _yaml

_CONFIG = None
_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "app", "config", "scoring_config.yaml")
FORMULA_VERSION = "explainable-v2"


def get_config():
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG
    cfg = {}
    try:
        with open(_CONFIG_PATH) as f:
            cfg = _yaml.safe_load(f) or {}
    except Exception:
        pass
    # Validate overall coefficients.
    ov = cfg.get("overall", {})
    tot = ov.get("keyword_weight", .6) + ov.get("semantic_weight", .3) + ov.get("experience_weight", .1)
    if abs(tot - 1.0) > 1e-6:
        raise ValueError(f"Invalid scoring_config: overall weights sum to {tot}, must be 1.0")
    rl = cfg.get("requirement_level", {})
    rtot = rl.get("keyword_weight", .35) + rl.get("semantic_weight", .35) + rl.get("evidence_weight", .30)
    if abs(rtot - 1.0) > 1e-6:
        raise ValueError(f"Invalid scoring_config: requirement weights sum to {rtot}, must be 1.0")
    cfg.setdefault("config_version", "2.0.0")
    _CONFIG = cfg
    return cfg


def keyword_value(kind, cfg=None):
    cfg = cfg or get_config()
    return float(cfg.get("keyword", {}).get(kind, {"exact": 1.0, "fuzzy_skill": 0.7,
                                                   "partial": 0.5, "missing": 0.0}.get(kind, 0.0)))


def score_keyword(requirement, resume_skills, fuzzy_hits=None, cfg=None):
    """K_i with word-boundary/alias rules; short acronyms never substring-match."""
    cfg = cfg or get_config()
    kw = cfg.get("keyword", {})
    canon = requirement.get("canonical_skill", "")
    resume_set = {normalize_skill(s) for s in (resume_skills or [])}
    if canon in resume_set:
        return kw.get("exact", 1.0), "explicit match"
    # A concrete technology satisfies a generic requirement (MySQL -> SQL),
    # credited at the fuzzy level so it never outranks a direct mention.
    domain_hits = sorted(s for s in resume_set if domain_covers(canon, s))
    if domain_hits and canon:
        return kw.get("fuzzy_skill", 0.7), (
            f"domain match ({', '.join(domain_hits[:3])} implements {canon})")
    if fuzzy_hits and canon in fuzzy_hits:
        info = fuzzy_hits[canon]
        if info.get("via") == "raw_text":
            return kw.get("partial", 0.5), f"typo recovery ({info.get('found_as')})"
        return kw.get("fuzzy_skill", 0.7), f"variant match ({info.get('found_as')})"
    return kw.get("missing", 0.0), "no valid keyword match"


def transform_cosine(raw, cfg=None):
    """Documented normalization: clip01 -> M = max(0, raw). Returns (M, name)."""
    cfg = cfg or get_config()
    name = (cfg.get("semantic", {}) or {}).get("transform", "clip01")
    if name == "clip01":
        return round(max(0.0, min(1.0, float(raw))), 4), "clip01(max(0,cos))"
    return round(max(0.0, min(1.0, float(raw))), 4), name


def semantic_band(normalized, cfg=None):
    """Calibrated descriptor for a normalized semantic score.

    A cosine similarity is NOT a probability or a proficiency percentage, so it
    is reported with a qualitative band rather than as a bare percentage.
    Thresholds come from scoring_config.yaml (semantic.*_threshold).
    """
    cfg = cfg or get_config()
    sem = cfg.get("semantic", {}) or {}
    strong = sem.get("strong_threshold", 0.75)
    moderate = sem.get("moderate_threshold", 0.50)
    weak = sem.get("weak_threshold", 0.25)
    v = float(normalized or 0.0)
    if v >= strong:
        band = "strong"
    elif v >= moderate:
        band = "moderate"
    elif v >= weak:
        band = "weak"
    else:
        band = "minimal"
    return {"band": band, "thresholds": {"strong": strong, "moderate": moderate,
                                         "weak": weak},
            "interpretation": "cosine similarity band, not a probability"}


def score_semantic(requirement, evidence_records, resume_text, cfg=None):
    """M for one requirement: best cosine over ITS OWN evidence passages.

    Compares the requirement ONLY against evidence records that actually
    mention the same canonical skill. The previous implementation fell back to
    12 arbitrary resume chunks when a skill had no evidence, which produced a
    non-zero M for absent skills (inflating/deflating the aggregate M against
    the requirement table) and made M non-reproducible between runs.

    If no supporting passage exists, M = 0.0 with basis "no-evidence". A raw
    cosine is never invented from unrelated text.
    """
    cfg = cfg or get_config()
    desc = requirement.get("description", "") or requirement.get("canonical_skill", "")
    model_name = _jm.get_embed_model_name()
    if not model_name:
        return {"raw_cosine": 0.0, "normalized": 0.0, "matched_passage": "",
                "model": None, "basis": "model-unavailable",
                "error": "embedding model unavailable", "embedding_dim": None}

    skill = requirement.get("canonical_skill")
    cands = [e for e in (evidence_records or [])
             if e.get("canonical_skill") == skill
             and not e.get("is_negated") and not e.get("is_duplicate")]
    batch = [(e.get("original_text") or "")[:2000] for e in cands[:16]]
    batch = [b for b in batch if b.strip()]
    if not batch:
        # Honest zero: this skill has no supporting passage in the resume.
        return {"raw_cosine": 0.0, "normalized": 0.0, "matched_passage": "",
                "model": model_name, "basis": "no-evidence",
                "embedding_dim": None}

    try:
        q = _jm._embed_batch([desc[:2000]])
        mat = _jm._embed_batch(batch)
        sims = (mat @ q.T).ravel()
        j = int(sims.argmax())
        best_raw = float(sims[j])
        norm, tname = transform_cosine(best_raw, cfg)
        return {"raw_cosine": round(best_raw, 4), "normalized": norm,
                "transform": tname, "matched_passage": batch[j][:500],
                "basis": "skill-evidence", "candidates_scored": len(batch),
                "evidence_ids": [e.get("evidence_id") for e in cands[:16]
                                 if (e.get("original_text") or "")[:2000] == batch[j]],
                "band": semantic_band(norm, cfg)["band"],
                "model": model_name, "embedding_dim": int(q.shape[1])}
    except Exception as e:
        return {"raw_cosine": 0.0, "normalized": 0.0, "matched_passage": "",
                "model": model_name, "basis": "inference-error",
                "error": str(e)[:200], "embedding_dim": None}


def score_evidence(requirement, evidence_records, cfg=None):
    """P_i rubric: best non-duplicate, non-negated evidence decides."""
    cfg = cfg or get_config()
    ev = cfg.get("evidence", {})
    cands = [e for e in (evidence_records or [])
             if e.get("canonical_skill") == requirement.get("canonical_skill")]
    if not cands:
        return ev.get("no_evidence", 0.0), "no supporting evidence found", None
    pos = [e for e in cands if not e.get("is_negated") and not e.get("is_duplicate")]
    pool = pos or [e for e in cands if not e.get("is_negated")] or cands
    if all(e.get("is_negated") for e in cands):
        return ev.get("negated_value", 0.0), "skill explicitly negated in resume", cands[0]
    best, best_lv = None, -1
    for e in pool:
        t = (e.get("evidence_type") or "")
        if t in ("project_implementation", "work_implementation"):
            lv = ev.get("demonstrated_implementation", 1.0)
        elif t in ("work_experience", "certification"):
            lv = ev.get("specific_practical_use", 0.7)
        else:
            lv = ev.get("mentioned_only", 0.3)
        if e.get("is_planned"):
            lv = min(lv, ev.get("planned_work_cap", 0.3))
        if lv > best_lv:
            best, best_lv = e, lv
    reason = {"project_implementation": "concrete implementation described",
              "work_implementation": "concrete work implementation described",
              "work_experience": "meaningful practical use, limited depth",
              "certification": "certification evidence",
              }.get(best.get("evidence_type"), "mentioned without implementation evidence")
    if best.get("is_planned"):
        reason += " (planned/aspirational — capped)"
    return round(float(best_lv), 2), reason, best


def score_requirement(requirement, resume_skills, evidence_records, resume_text,
                      fuzzy_hits=None, cfg=None):
    """R_i = 100*(0.35K + 0.35M + 0.30P) with full trace."""
    cfg = cfg or get_config()
    rl = cfg.get("requirement_level", {})
    wk, wm, wp = rl.get("keyword_weight", .35), rl.get("semantic_weight", .35), rl.get("evidence_weight", .30)
    k, k_why = score_keyword(requirement, resume_skills, fuzzy_hits, cfg)
    sem = score_semantic(requirement, evidence_records, resume_text, cfg)
    m = sem["normalized"]
    p, p_why, best_ev = score_evidence(requirement, evidence_records, cfg)
    r = round(100 * (wk * k + wm * m + wp * p), 1)
    status = ("matched" if r >= 70 else "partial" if r >= 40 else
              "uncertain" if sem.get("error") else "missing")
    return {
        "requirement_id": requirement.get("requirement_id"),
        "name": requirement.get("name"),
        "category": requirement.get("category"),
        "canonical_skill": requirement.get("canonical_skill"),
        "importance_weight": requirement.get("importance_weight", 3),
        "weight_source": requirement.get("weight_source", "inferred"),
        "classification": requirement.get("classification"),
        "requirement_type": requirement.get("requirement_type", "technical_skill"),
        "mandatory": requirement.get("mandatory", False),
        "mandatory_source": requirement.get("mandatory_source", ""),
        "mandatory_explicit": requirement.get("mandatory_explicit", False),
        "weight_inferred": requirement.get("weight_inferred", False),
        "weight_reason": requirement.get("weight_reason", ""),
        "keyword_match": k, "keyword_note": k_why,
        "raw_cosine": sem.get("raw_cosine"),
        "raw_cosine_note": "cosine similarity in [-1,1]; not a probability",
        "semantic_score": m,
        "semantic_band": sem.get("band"),
        "semantic_basis": sem.get("basis"),
        "semantic_transform": sem.get("transform"),
        "semantic_candidates_scored": sem.get("candidates_scored"),
        "semantic_evidence_ids": sem.get("evidence_ids"),
        "semantic_model": sem.get("model"),
        "semantic_error": sem.get("error"),
        "evidence_strength": p, "evidence_note": p_why,
        "requirement_score": r, "status": status,
        # Exact inputs so R_i can be recomputed by any consumer.
        "formula_inputs": {"K": k, "M": m, "P": p,
                           "w_keyword": wk, "w_semantic": wm, "w_evidence": wp},
        "resume_evidence": (best_ev or {}).get("original_text", "") or sem.get("matched_passage", ""),
        "evidence_section": (best_ev or {}).get("section", ""),
        "evidence_type": (best_ev or {}).get("evidence_type"),
        "evidence_page": (best_ev or {}).get("page_number"),
        "source_text": requirement.get("source_text", ""),
        "recommended_action": "",
    }


def _experience_fit(actual_years, required_years, cfg=None, profile=None):
    """Experience-fit score + its inputs (E) and the reasoning behind it.

    Professional and project experience stay separate: a 2-month internship is
    professional experience, a university project is not. Confidence is reported
    so the UI can distinguish "no experience" from "experience not detected".
    """
    cfg = cfg or get_config()
    exp_cfg = cfg.get("experience", {}) or {}
    score = _jm.calculate_experience_score(actual_years or 0, required_years or 0)
    detail = dict(profile or {})
    detected = bool(actual_years)
    return float(score), {
        "actual_years": actual_years,
        "required_years": required_years,
        "score": float(score),
        "detected": detected,
        "confidence": detail.get("confidence", "none"),
        "basis": detail.get("basis", ""),
        "trainee_friendly": bool(exp_cfg.get("trainee_friendly", True)),
        # Professional vs project experience stay separate in the report.
        "professional_years": detail.get("professional_years", actual_years),
        "project_years": detail.get("project_years", 0.0),
        "education_years": detail.get("education_years", 0.0),
        "employment_section_found": detail.get("employment_section_found"),
        "roles": detail.get("roles", [])[:5],
        "evidence": detail.get("evidence", []),
        "unverifiable": detail.get("unverifiable", []),
        "note": detail.get("note", ""),
    }


def aggregate(requirement_scores, experience_score, cfg=None,
              experience_detail=None):
    """Overall hybrid S (primary) + requirement-weighted (diagnostic).

    This is the SINGLE authoritative aggregation. Every number returned here
    is computed once, from the same per-requirement rows that the report,
    API and frontend render — no consumer recomputes it.
    """
    cfg = cfg or get_config()
    ov = cfg.get("overall", {})
    if not requirement_scores:
        return {"status": "insufficient-data",
                "reason": "no scoreable requirements extracted from JD"}
    tot_w = sum(r.get("importance_weight", 1) for r in requirement_scores) or 0
    if tot_w <= 0:
        return {"status": "insufficient-data", "reason": "zero total requirement weight"}
    wk, wm, we = (ov.get("keyword_weight", .6), ov.get("semantic_weight", .3),
                  ov.get("experience_weight", .1))
    K = 100 * sum(r["importance_weight"] * r["keyword_match"] for r in requirement_scores) / tot_w
    M = 100 * sum(r["importance_weight"] * r["semantic_score"] for r in requirement_scores) / tot_w
    E = float(experience_score)
    # Blend at FULL precision. Rounding the components before blending made the
    # headline disagree with the displayed breakdown (59.3 vs 59.44).
    S = wk * K + wm * M + we * E
    req_w = sum(r["importance_weight"] * r["requirement_score"] for r in requirement_scores) / tot_w
    matched = sum(1 for r in requirement_scores if r["keyword_match"] >= 1.0)
    return {
        "status": "ok",
        # Presentation values (rounded for display only).
        "keyword_coverage": round(K, 1),
        "semantic_relevance": round(M, 1),
        "experience_fit": round(E, 1),
        "overall_score": round(S, 1),
        "requirement_weighted_score": round(req_w, 1),
        "unweighted_skill_coverage": round(100 * matched / len(requirement_scores), 1),
        "formula": "S=%.2f*K+%.2f*M+%.2f*E; R_i=100*(%.2fK_i+%.2fM_i+%.2fP_i)"
                   % (wk, wm, we,
                      cfg.get("requirement_level", {}).get("keyword_weight", .35),
                      cfg.get("requirement_level", {}).get("semantic_weight", .35),
                      cfg.get("requirement_level", {}).get("evidence_weight", .30)),
        "formula_version": FORMULA_VERSION,
        "config_version": cfg.get("config_version"),
        "weights": {"keyword": wk, "semantic": wm, "experience": we},
        # Exact, unrounded formula inputs: S = w_k*K + w_m*M + w_e*E reproduces
        # `overall_score` exactly, with no rounding discrepancy.
        "formula_inputs": {
            "K": K, "M": M, "E": E, "overall_exact": S,
            "w_keyword": wk, "w_semantic": wm, "w_experience": we,
            "requirement_count": len(requirement_scores),
            "total_weight": tot_w,
        },
        "rounding": {
            "applied_at": "presentation only",
            "dp": 1,
            "note": "Components and S are computed at full precision. Displayed "
                    "values are rounded to 1 dp; rounding never feeds back into "
                    "the calculation.",
        },
        "experience_detail": experience_detail or {},
    }


def analyze(requirements, resume_skills, evidence_records, resume_text,
            experience_years, required_experience, fuzzy_hits=None, cfg=None,
            weights_override=None, experience_profile=None):
    """Full pipeline: per-requirement scores + aggregate + audit stub."""
    cfg = dict(cfg or get_config())
    if weights_override:
        ov = dict(cfg.get("overall", {}))
        _alias = {"keyword": "keyword_weight", "semantic": "semantic_weight",
                  "experience": "experience_weight"}
        for k, v in weights_override.items():
            k = _alias.get(k, k)
            if k in ov:
                ov[k] = float(v)
            else:
                raise ValueError(f"unknown weight '{k}' (use keyword/semantic/experience)")
        if abs(sum(ov.values()) - 1.0) > 1e-6:
            raise ValueError("recalculate weights must sum to 1.0")
        cfg["overall"] = ov
    if not requirements:
        return {"status": "insufficient-data",
                "reason": "no scoreable requirements extracted from JD",
                "requirements": [], "config_version": cfg.get("config_version")}
    t0 = time.time()
    req_scores = [score_requirement(r, resume_skills, evidence_records, resume_text,
                                    fuzzy_hits, cfg) for r in (requirements or [])]
    exp_score, exp_detail = _experience_fit(experience_years or 0,
                                            required_experience or 0, cfg,
                                            profile=experience_profile)
    agg = aggregate(req_scores, exp_score, cfg, experience_detail=exp_detail)
    if agg.get("status") != "ok":
        return {"status": agg["status"], "reason": agg.get("reason"),
                "requirements": req_scores, "config_version": cfg.get("config_version")}
    for r in req_scores:
        r["recommended_action"] = ""  # filled by report_generator gaps
    return {"status": "ok", "requirements": req_scores, "aggregate": agg,
            "experience_score": exp_score, "compute_ms": int((time.time() - t0) * 1000),
            "formula_version": FORMULA_VERSION, "config_version": cfg.get("config_version")}
