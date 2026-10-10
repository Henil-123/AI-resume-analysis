"""Eligibility and mandatory-requirement checks (spec section 14).

States: VERIFIED_ELIGIBLE / VERIFIED_INELIGIBLE / UNKNOWN.
Missing information is NEVER a verified failure.
"""

EDU_ORDER = {"not specified": 0, "diploma/certificate": 1, "bachelors": 2,
             "masters": 3, "phd": 4}


def _edu_rank(name):
    return EDU_ORDER.get((name or "not specified").lower(), 0)


def check_eligibility(requirements, evidence_records, resume_education,
                      experience_years, required_experience, jd_meta=None):
    """Evaluate each mandatory criterion separately from the weighted score."""
    criteria = []
    overall = "VERIFIED_ELIGIBLE"
    jd_meta = jd_meta or {}

    # Degree bar (only when JD names an explicit requirement).
    req_edu = (jd_meta.get("education_required") or "")
    if req_edu and req_edu != "Not specified":
        if not resume_education or resume_education == "Not specified":
            criteria.append({"criterion": f"Education: {req_edu} required",
                             "status": "UNKNOWN",
                             "detail": "No education detected in resume — cannot verify."})
            overall = "UNKNOWN" if overall == "VERIFIED_ELIGIBLE" else overall
        elif _edu_rank(resume_education) >= _edu_rank(req_edu):
            criteria.append({"criterion": f"Education: {req_edu} required",
                             "status": "VERIFIED_ELIGIBLE",
                             "detail": f"Resume shows {resume_education}."})
        else:
            criteria.append({"criterion": f"Education: {req_edu} required",
                             "status": "VERIFIED_INELIGIBLE",
                             "detail": f"Resume shows {resume_education}, below {req_edu}."})
            overall = "VERIFIED_INELIGIBLE"

    # Experience bar (only when JD states an explicit minimum AND resume
    # has detectable years; otherwise UNKNOWN).
    if (required_experience or 0) > 0:
        if (experience_years or 0) <= 0:
            criteria.append({"criterion": f"Experience: {required_experience}+ years",
                             "status": "UNKNOWN",
                             "detail": "No experience years detected — cannot verify."})
            if overall == "VERIFIED_ELIGIBLE":
                overall = "UNKNOWN"
        elif experience_years >= required_experience:
            criteria.append({"criterion": f"Experience: {required_experience}+ years",
                             "status": "VERIFIED_ELIGIBLE",
                             "detail": f"Resume shows ~{experience_years} years."})
        else:
            criteria.append({"criterion": f"Experience: {required_experience}+ years",
                             "status": "UNKNOWN",
                             "detail": (f"Resume shows ~{experience_years} years vs "
                                        f"{required_experience} required — shortfall, not a formal bar "
                                        f"unless the JD marks it mandatory.")})

    # Mandatory skills: missing == skill gap; formal failure only when the JD
    # explicitly marks it mandatory AND no evidence exists at all.
    for r in requirements or []:
        if not r.get("mandatory"):
            continue
        ev = [e for e in (evidence_records or [])
              if e.get("canonical_skill") == r.get("canonical_skill")
              and not e.get("is_negated")]
        if ev:
            criteria.append({"criterion": f"Mandatory skill: {r.get('name')}",
                             "status": "VERIFIED_ELIGIBLE",
                             "detail": f"Evidence found in {ev[0].get('section')}."})
        elif r.get("mandatory_source", "").startswith("explicit"):
            criteria.append({"criterion": f"Mandatory skill: {r.get('name')}",
                             "status": "VERIFIED_INELIGIBLE",
                             "detail": "Explicitly mandatory per JD; no evidence found."})
            overall = "VERIFIED_INELIGIBLE"
        else:
            criteria.append({"criterion": f"Skill gap: {r.get('name')}",
                             "status": "UNKNOWN",
                             "detail": "No evidence found; importance inferred, not a formal bar."})
            if overall == "VERIFIED_ELIGIBLE":
                overall = "UNKNOWN"

    if not criteria:
        criteria.append({"criterion": "No explicit eligibility bars detected",
                         "status": "UNKNOWN",
                         "detail": "JD states no verifiable mandatory conditions."})
        overall = "UNKNOWN"
    return {"overall": overall, "criteria": criteria}
