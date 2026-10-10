"""JD -> structured requirements (spec section 5).

Each requirement: requirement_id, name, category, description,
importance_weight (1-5), mandatory (+source explicit/inferred),
requirement_type, canonical_skill, aliases, expected_evidence,
source_page, source_text, weight_inferred (+reason).
"""
import re

try:
    from services.skill_aliases import normalize_skill, normalize_skill_list
    from services.skill_extractor import (
        extract_required_skills_from_jd, extract_skills_with_categories)
    from services.section_parser import parse_jd_sections
except ImportError:
    from skill_aliases import normalize_skill, normalize_skill_list
    from skill_extractor import (
        extract_required_skills_from_jd, extract_skills_with_categories)
    from section_parser import parse_jd_sections

CATEGORY_BY_TAXONOMY = {}  # filled lazily from skill_category_map


def _category_of(skill):
    try:
        try:
            from services.skill_extractor import skill_category_map
        except ImportError:
            from skill_extractor import skill_category_map
        return skill_category_map.get(skill, "general")
    except Exception:
        return "general"


def _page_for_line(jd_meta, line_idx):
    """Page number for a JD line, from jd_meta's page map (never invented)."""
    pages = (jd_meta or {}).get("line_pages") or []
    if pages and 0 <= line_idx < len(pages):
        return pages[line_idx]
    return (jd_meta or {}).get("default_page", 1)


def _mandatory_markers(line):
    low = line.lower()
    explicit = any(k in low for k in (
        "must have", "must-have", "mandatory", "required", "minimum qualification",
        "basic qualification", "eligibility", "essential"))
    preferred = any(k in low for k in (
        "preferred", "nice to have", "bonus", "plus", "desired", "good to have"))
    return explicit, preferred


# Requirement classes. Only EXPLICIT_MANDATORY may influence eligibility;
# everything else is a priority that feeds the compatibility score.
EXPLICIT_MANDATORY = "explicit_mandatory"
LEARNING_OUTCOME = "required_learning_outcome"
PREFERRED = "preferred"
BEHAVIOURAL = "behavioural_competency"
INFERRED = "inferred_priority"

# Skills that are never a technical must-have on their own: they describe
# working style or tooling expectations, not role-defining capability.
BEHAVIOURAL_SKILLS = {
    "communication", "teamwork", "leadership", "collaboration", "problem solving",
    "problem-solving", "presentation", "ownership", "attention to detail",
    "time management", "analytical thinking", "critical thinking",
}
DEVOPS_AWARENESS = {"devops", "monitoring", "ci/cd", "logging", "observability",
                    "infrastructure as code"}

# Equivalent/duplicate skills that must collapse into ONE requirement so the
# requirement count and the weighted average are not double counted.
DUPLICATE_GROUPS = [
    {"c", "c#", "c++", "java"},          # kept separate, listed for clarity
    {"asp.net", ".net", "dotnet"},       # alias set, one entry only
    {"rest api", "rest apis", "restful", "restful api", "restful apis"},
    {"ci/cd", "ci-cd", "cicd", "continuous integration"},
]


def _dedupe_key(canon):
    for grp in DUPLICATE_GROUPS:
        if canon in grp:
            # 'c' and 'c#' are distinct languages: do not merge them.
            if canon in ("c", "c++", "java"):
                return canon
            return sorted(grp)[0]
    return canon


def _classify(canon, in_must, in_pref, explicit_marker, count):
    """Return (classification, mandatory, mandatory_explicit, weight, reason)."""
    if canon in BEHAVIOURAL_SKILLS:
        return (BEHAVIOURAL, False, False, 2,
                "inferred: behavioural competency, not an eligibility rule")
    if in_pref and not in_must and not explicit_marker:
        return (PREFERRED, False, False, 2,
                "explicit: listed under preferred/nice-to-have section")
    if in_must or explicit_marker:
        if canon in DEVOPS_AWARENESS:
            # "Exposure to DevOps/CI/CD/monitoring" is awareness, not a bar.
            return (LEARNING_OUTCOME, False, False, 3,
                    "inferred: DevOps/monitoring awareness, not an eligibility bar")
        return (EXPLICIT_MANDATORY, True, True, 5,
                "explicit: listed under required/must-have section"
                if in_must else
                "explicit: mandatory wording on the requirement line")
    return (INFERRED, False, False, 4 if count >= 3 else 3,
            f"inferred: {'high frequency' if count >= 3 else 'default'} priority "
            f"({count} mention(s)); no mandatory wording in the JD")


def extract_requirements(jd_text, jd_meta=None):
    """Convert JD text into deduplicated structured requirements.

    Mandatory is ONLY set when the JD explicitly says so (required/must-have
    section or an explicit marker on the skill's own line). Everything else is
    an inferred priority. Previously, a JD with no recognised "required" or
    "preferred" header marked EVERY extracted skill mandatory — which is how
    unrelated technologies (e.g. ASP.NET on a Java Full Stack role) became
    must-haves, and why eligibility could fail a candidate for a skill the
    source never required.
    """
    jd_text = jd_text or ""
    meta = jd_meta or {}
    sections = parse_jd_sections(jd_text)
    try:
        counts = extract_required_skills_from_jd(jd_text, with_counts=True)
    except TypeError:
        counts = {s: 1 for s in extract_required_skills_from_jd(jd_text)}
    req_section_skills = {normalize_skill(s) for s in
                          (extract_required_skills_from_jd(sections.get("required", "")) or [])}
    pref_section_skills = {normalize_skill(s) for s in
                           (extract_required_skills_from_jd(sections.get("preferred", "")) or [])}

    lines = jd_text.split("\n")

    def first_line_for(canon):
        """The JD line that mentions this skill + the page it appears on."""
        for idx, line in enumerate(lines):
            low = line.lower()
            if (canon in low or canon.replace("_", " ") in low
                    or canon.replace(" ", "_") in low):
                return line.strip()[:300], _page_for_line(jd_meta, idx)
        return "", jd_meta.get("default_page", 1)

    source_text, source_page = "", (jd_meta or {}).get("default_page", 1)

    requirements = []
    emitted_keys = set()
    for i, (skill, count) in enumerate(
            sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])), start=1):
        canon = normalize_skill(skill)
        # Collapse alias duplicates so one capability is not counted twice.
        dkey = _dedupe_key(canon)
        if dkey in emitted_keys:
            continue
        in_must = canon in req_section_skills
        in_pref = canon in pref_section_skills
        source_text, source_page = first_line_for(canon)
        explicit_marker, _pref_marker = _mandatory_markers(source_text)
        classification, mandatory, mandatory_explicit, weight, w_reason = _classify(
            canon, in_must, in_pref, explicit_marker, count)
        emitted_keys.add(dkey)
        source = w_reason

        requirements.append({
            "requirement_id": f"req_{i:03d}",
            "name": canon.replace("_", " ").title() if "_" in canon else canon.title(),
            "category": _category_of(canon),
            "description": f"Demonstrated capability in {canon} as required by the JD",
            "importance_weight": weight,
            # Whether this weight was stated by the JD or inferred by the model.
            "weight_source": "explicit" if classification in (
                EXPLICIT_MANDATORY, PREFERRED) else "inferred",
            "classification": classification,
            "mandatory": mandatory,
            "mandatory_source": source,
            "mandatory_explicit": bool(mandatory_explicit),
            "requirement_type": ("behavioural" if classification == BEHAVIOURAL
                                 else "technical_skill"),
            "canonical_skill": canon,
            "aliases": [],
            "expected_evidence": ["project", "work_experience"],
            "source_page": source_page,
            "source_text": source_text,
            "weight_inferred": classification not in (EXPLICIT_MANDATORY, PREFERRED),
            "weight_reason": w_reason,
            "mention_count": count,
        })
    # Re-number after duplicate collapsing.
    for i, r in enumerate(requirements, start=1):
        r["requirement_id"] = f"req_{i:03d}"
    return requirements
