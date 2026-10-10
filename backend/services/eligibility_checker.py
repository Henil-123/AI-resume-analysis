"""Eligibility evaluation, kept strictly separate from compatibility scoring.

States: VERIFIED_ELIGIBLE / VERIFIED_INELIGIBLE / UNKNOWN.

Design rules enforced here:

  1. A MISSING SKILL is a compatibility gap, never an eligibility failure.
     VERIFIED_INELIGIBLE requires a JD eligibility rule (degree bar, batch
     year, backlog status, work authorisation) that is explicitly stated in
     the source AND demonstrably violated by the resume. Previously any
     missing mandatory skill produced INELIGIBLE, which contradicted the
     individual criteria shown alongside it.
  2. Missing information yields UNKNOWN, never a failure.
  3. Every criterion carries a reason and a source reference so a reviewer
     can check the decision against the JD.
  4. No eligibility rule is invented. If the JD states no rule, the result is
     UNKNOWN — never a fabricated Diploma/Certificate requirement.
"""
import re

try:
    from services.skill_aliases import normalize_skill
except ImportError:
    from skill_aliases import normalize_skill

EDU_ORDER = {"not specified": 0, "diploma/certificate": 1, "bachelors": 2,
             "masters": 3, "phd": 4}

# Explicit eligibility rules recognised in a JD, with the pattern that proves
# the JD actually stated them. No rule => no criterion is created.
_BATCH_RE = re.compile(r"(20\d{2})\s*(?:or|/)\s*(20\d{2})\s*batch|batch\s*(?:of)?\s*(20\d{2})", re.I)
_BACKLOG_RE = re.compile(r"no\s+active\s+backlogs?|active\s+backlogs?\s*(?:are|must)", re.I)
_BACKLOG_ZERO_RE = re.compile(r"(?:no|zero|0)\s+backlogs?", re.I)
_LOCATION_RE = re.compile(r"(?:must be|should be)\s+(?:available\s+)?(?:to\s+)?relocate", re.I)


def _edu_rank(name):
    return EDU_ORDER.get((name or "not specified").lower(), 0)


def _jd_rule_sources(jd_meta, jd_text=""):
    """Find eligibility rules actually stated in the JD, with their text."""
    text = jd_text or (jd_meta.get("job_description") or "")
    rules = {"batch_year": None, "backlogs": None, "location": None}
    m = _BATCH_RE.search(text)
    if m:
        years = [g for g in m.groups() if g]
        rules["batch_year"] = {"years": sorted(set(years)),
                               "source_text": m.group(0)[:160]}
    if _BACKLOG_ZERO_RE.search(text):
        rules["backlogs"] = {"required": "none",
                             "source_text": m.group(0)[:160] if m else ""}
        mb = re.search(r"[^.\n]*backlogs?[^.\n]*", text, re.I)
        rules["backlogs"]["source_text"] = (mb.group(0)[:160] if mb
                                            else "no active backlogs stated")
    if _LOCATION_RE.search(text):
        ml = re.search(r"[^.\n]*relocat[^.\n]*", text, re.I)
        rules["location"] = {"source_text": ml.group(0)[:160] if ml else ""}
    return rules


def _skill_evidence(requirement, evidence_records):
    skill = requirement.get("canonical_skill")
    alias = normalize_skill(skill)
    for e in evidence_records or []:
        if e.get("canonical_skill") in (skill, alias) and not e.get("is_negated"):
            return e
    return None


def check_eligibility(requirements, evidence_records, resume_education,
                      experience_years, required_experience, jd_meta=None,
                      jd_text=""):
    """Evaluate explicit, source-backed eligibility rules.

    Returns {overall, criteria, skill_gaps, source_rules}. Skill gaps are
    reported separately and never influence `overall`.
    """
    criteria = []
    skill_gaps = []
    jd_meta = jd_meta or {}

    def add(criterion, status, detail, source=None, rule=None):
        criteria.append({
            "criterion": criterion, "status": status, "detail": detail,
            "source": source or "", "rule": rule or "",
        })

    rules = _jd_rule_sources(jd_meta, jd_text)
    exp_detail = jd_meta.get("experience_detail") or {}

    # ── Degree bar (only when the JD states one) ──────────────────────
    req_edu = (jd_meta.get("education_required") or "").strip()
    edu_source = jd_meta.get("education_source_text") or ""
    if req_edu and req_edu.lower() != "not specified":
        if not resume_education or resume_education.lower() == "not specified":
            add(f"Education: {req_edu} required", "UNKNOWN",
                "No degree detected in the resume — the requirement cannot be "
                "verified either way.",
                edu_source or f"JD states: {req_edu}", "education")
        elif _edu_rank(resume_education) >= _edu_rank(req_edu):
            add(f"Education: {req_edu} required", "VERIFIED_ELIGIBLE",
                f"Resume states {resume_education}, which meets the stated minimum.",
                edu_source or f"JD states: {req_edu}", "education")
        else:
            add(f"Education: {req_edu} required", "VERIFIED_INELIGIBLE",
                f"Resume states {resume_education}, below the JD minimum of {req_edu}.",
                edu_source or f"JD states: {req_edu}", "education")

    # ── Batch / graduation year (only when stated) ─────────────────────
    if rules.get("batch_year"):
        years = rules["batch_year"]["years"]
        cand_year = jd_meta.get("candidate_graduation_year")
        if not cand_year:
            add(f"Graduation batch: {' or '.join(years)}", "UNKNOWN",
                "The resume does not state a graduation year, so batch "
                "eligibility cannot be verified.",
                rules["batch_year"]["source_text"], "batch_year")
        else:
            if str(cand_year) in years:
                add(f"Graduation batch: {' or '.join(years)}", "VERIFIED_ELIGIBLE",
                    f"Candidate is a {cand_year} graduate.",
                    rules["batch_year"]["source_text"], "batch_year")
            else:
                add(f"Graduation batch: {' or '.join(years)}", "VERIFIED_INELIGIBLE",
                    f"Candidate graduated in {cand_year}; the JD targets the "
                    f"{' or '.join(years)} batch.",
                    rules["batch_year"]["source_text"], "batch_year")

    # ── Active backlogs (only when stated) ─────────────────────────────
    if rules.get("backlogs"):
        backlog = (jd_meta.get("candidate_active_backlogs") or "").strip()
        if backlog == "":
            add("Active backlogs", "UNKNOWN",
                "The resume does not state backlog status.",
                rules["backlogs"]["source_text"], "backlogs")
        elif re.match(r"^(none|no|zero|0|nil)\b", backlog, re.I):
            add("Active backlogs", "VERIFIED_ELIGIBLE",
                "Resume states no active backlogs.",
                rules["backlogs"]["source_text"], "backlogs")
        else:
            add("Active backlogs", "VERIFIED_INELIGIBLE",
                f"Resume states active backlogs ('{backlog[:60]}') while the JD "
                "requires none.",
                rules["backlogs"]["source_text"], "backlogs")

    # ── Explicit experience bar (only when the JD states one) ──────────
    if (required_experience or 0) > 0:
        if (experience_years or 0) <= 0:
            add(f"Experience: {required_experience}+ years", "UNKNOWN",
                "No professional experience duration could be verified — an "
                "unknown, not a confirmed shortfall.",
                f"JD requires {required_experience}+ years", "experience")
        elif experience_years >= required_experience:
            add(f"Experience: {required_experience}+ years", "VERIFIED_ELIGIBLE",
                f"Resume evidences {experience_years} years.",
                f"JD requires {required_experience}+ years", "experience")
        else:
            # A shortfall against a soft bar is not a formal disqualification.
            add(f"Experience: {required_experience}+ years", "UNKNOWN",
                f"Resume evidences {experience_years} years against a stated "
                f"{required_experience}+ years. The JD does not state this as a "
                "disqualifying eligibility rule, so this is a gap, not a failure.",
                f"JD requires {required_experience}+ years", "experience")

    # ── Skills: gaps only, never eligibility failures ──────────────────
    for r in requirements or []:
        if not r.get("mandatory"):
            continue
        ev = _skill_evidence(r, evidence_records)
        if ev:
            add(f"Mandatory skill: {r.get('name')}", "VERIFIED_ELIGIBLE",
                f"Evidence found in the {ev.get('section')} section.",
                r.get("source_text") or r.get("mandatory_source") or "",
                "mandatory_skill")
        else:
            # Explicitly a gap: recorded, never promoted to INELIGIBLE.
            skill_gaps.append({
                "skill": r.get("name"),
                "canonical_skill": r.get("canonical_skill"),
                "importance_weight": r.get("importance_weight"),
                "mandatory_source": r.get("mandatory_source"),
                "detail": "No supporting evidence found in the resume. This is "
                          "a skills gap affecting the compatibility score, not "
                          "an eligibility failure.",
            })

    # ── Overall ────────────────────────────────────────────────────────
    states = [c["status"] for c in criteria]
    if "VERIFIED_INELIGIBLE" in states:
        overall = "VERIFIED_INELIGIBLE"
    elif states and all(s == "VERIFIED_ELIGIBLE" for s in states):
        overall = "VERIFIED_ELIGIBLE"
    elif states:
        overall = "UNKNOWN"
    else:
        overall = "UNKNOWN"
        add("No explicit eligibility rules detected", "UNKNOWN",
            "The JD states no verifiable eligibility conditions (degree, batch "
            "year, backlogs). Nothing can be affirmed or denied.",
            "", "none")

    return {
        "overall": overall,
        "criteria": criteria,
        "skill_gaps": skill_gaps,
        "source_rules": {k: v for k, v in rules.items() if v},
        "note": ("Eligibility is evaluated only against explicit, source-backed "
                 "JD rules. Skill gaps appear in `skill_gaps` and affect the "
                 "compatibility score only."),
    }