"""Gap analysis, recommendations and full screening report (spec 16-17)."""
import datetime

try:
    from services import job_matcher as _jm
except ImportError:
    import job_matcher as _jm


def analyze_gaps(requirement_scores):
    """Per-requirement gap records for low/uncertain scores."""
    gaps = []
    for r in requirement_scores or []:
        if r.get("requirement_score", 100) >= 70 and r.get("status") == "matched":
            continue
        missing = r.get("keyword_match", 0) == 0
        gaps.append({
            "requirement": r.get("name"),
            "importance": r.get("importance_weight"),
            "current_evidence": r.get("resume_evidence", "") or "none found",
            "reason": ("No valid keyword match; " if missing else "") +
                      (r.get("evidence_note", "") or "") +
                      (f" (semantic signal unavailable: {r.get('semantic_error')})"
                       if r.get("semantic_error") else ""),
            "missing_or_unverified": "missing" if missing and not r.get("semantic_error") else "unverified",
            "recommended_action": recommend_for(r),
        })
        r["recommended_action"] = gaps[-1]["recommended_action"]
    return gaps


def recommend_for(r):
    name = r.get("name", "this skill")
    if r.get("keyword_match", 0) == 0:
        return (f"Acquire {name}: build a small project demonstrating it "
                f"(implementation + tests), then add it to the resume only after doing so. "
                f"Never list unpracticed skills.")
    if r.get("evidence_strength", 0) <= 0.3:
        return (f"Strengthen {name} evidence: move it from the skills list into a project "
                f"bullet with outcome verbs (built/tested/deployed) and measurable results.")
    return (f"Improve {name} wording: quantify scope and ownership in the existing bullet.")


def build_report(candidate, requirements, requirement_scores, aggregate,
                 eligibility, evidence_records, jd_meta, gaps=None,
                 extraction_status="ok", errors=None):
    """Assemble the 8-section explainable report (A-H)."""
    gaps = gaps if gaps is not None else analyze_gaps(requirement_scores)
    matched = [r for r in requirement_scores if r.get("status") == "matched"]
    partial = [r for r in requirement_scores if r.get("status") == "partial"]
    missing = [r for r in requirement_scores if r.get("status") == "missing"]
    uncertain = [r for r in requirement_scores if r.get("status") == "uncertain"]
    strengths = [f"{r['name']} ({r['requirement_score']}%)" for r in matched[:5]]
    crit_gaps = [g["requirement"] for g in gaps
                 if any(x.get("name") == g["requirement"] and x.get("mandatory")
                        for x in requirement_scores)][:5]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        model_name = _jm.get_embed_model_name()
    except Exception:
        model_name = None
    try:
        from services.skill_extractor import SPACY_MODEL_NAME as _spacy
    except Exception:
        _spacy = "unknown"
    cfg_v = (aggregate or {}).get("config_version", "2.0.0")
    return {
        "executive_summary": {
            "overall_score": (aggregate or {}).get("overall_score"),
            "methodology": ("Primary S=0.60*K+0.30*M+0.10*E over requirement-level "
                            "R_i=100*(0.35K_i+0.35M_i+0.30P_i). See scoring_methodology.md."),
            "model": model_name, "evaluated_at": now,
            "eligibility": (eligibility or {}).get("overall", "UNKNOWN"),
            "main_strengths": strengths, "main_gaps": crit_gaps or [g["requirement"] for g in gaps[:3]],
            "confidence": ("high" if extraction_status == "ok" and model_name else "low"),
            "limitations": ("Heuristic screening support, not a validated hiring prediction. "
                            + ("; ".join(errors) if errors else "No extraction errors.")),
        },
        "score_breakdown": {
            "keyword_coverage": (aggregate or {}).get("keyword_coverage"),
            "semantic_relevance": (aggregate or {}).get("semantic_relevance"),
            "experience_fit": (aggregate or {}).get("experience_fit"),
            "requirement_weighted_score": (aggregate or {}).get("requirement_weighted_score"),
            "unweighted_skill_coverage": (aggregate or {}).get("unweighted_skill_coverage"),
        },
        "requirements": requirement_scores,
        "matched_skills": {
            "direct": [r["name"] for r in matched],
            "transferable_or_partial": [r["name"] for r in partial],
            "related_non_equivalent": [],
            "skills_section_only": [r["name"] for r in matched if r.get("evidence_strength", 1) <= 0.3],
        },
        "missing_skills": {
            "critical": [g["requirement"] for g in gaps
                         if any(x.get("name") == g["requirement"] and x.get("importance_weight", 0) >= 4
                                for x in requirement_scores)][:10],
            "important": [g["requirement"] for g in gaps
                          if any(x.get("name") == g["requirement"] and x.get("importance_weight", 0) == 3
                                 for x in requirement_scores)][:10],
            "secondary": [g["requirement"] for g in gaps
                          if any(x.get("name") == g["requirement"] and x.get("importance_weight", 0) <= 2
                                 for x in requirement_scores)][:10],
            "unverified": [r["name"] for r in uncertain],
        },
        "eligibility": eligibility,
        "recommendations": gaps,
        "technical_audit": {
            "model": model_name, "spacy": _spacy,
            "formula": (aggregate or {}).get("formula"),
            "formula_version": "explainable-v2",
            "config_version": cfg_v,
            "weights": (aggregate or {}).get("weights"),
            "thresholds": {"matched": 70, "partial": 40},
            "extraction_status": extraction_status,
            "evidence_count": len(evidence_records or []),
            "errors": errors or [],
            "scoring_timestamp": now,
        },
        "candidate": {"name": candidate.get("name"), "education": candidate.get("education"),
                      "experience_years": candidate.get("experience_years")},
        "jd": {"job_title": (jd_meta or {}).get("job_title", ""),
               "required_skills": (jd_meta or {}).get("required_skills", [])},
    }
