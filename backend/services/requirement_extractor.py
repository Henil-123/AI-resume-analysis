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


def _mandatory_markers(line):
    low = line.lower()
    explicit = any(k in low for k in (
        "must have", "must-have", "mandatory", "required", "minimum qualification",
        "basic qualification", "eligibility", "essential"))
    preferred = any(k in low for k in (
        "preferred", "nice to have", "bonus", "plus", "desired", "good to have"))
    return explicit, preferred


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
        """The JD line that actually mentions this skill (mandatory marker source)."""
        for line in lines:
            low = line.lower()
            if canon in low or canon.replace("_", " ") in low or canon.replace(" ", "_") in low:
                return line.strip()[:300]
        return ""

    requirements = []
    for i, (skill, count) in enumerate(
            sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])), start=1):
        canon = normalize_skill(skill)
        in_must = canon in req_section_skills
        in_pref = canon in pref_section_skills
        source_text = first_line_for(canon)
        explicit_marker, _pref_marker = _mandatory_markers(source_text)

        if in_pref:
            # Preferred never overrides an explicit required marker.
            if in_must or explicit_marker:
                mandatory = True
                source = "explicit: listed under required section or with mandatory wording"
                weight, w_reason = 5, "explicit: required"
            else:
                mandatory = False
                source = "explicit: listed under preferred/nice-to-have section"
                weight, w_reason = 2, "explicit: preferred section"
        elif in_must or explicit_marker:
            mandatory = True
            source = "explicit: listed under required/must-have section" if in_must else \
                     "explicit: mandatory wording on the requirement line"
            weight, w_reason = 5, "explicit: required"
        else:
            # NOT mandatory. Weight is an inferred priority only.
            mandatory = False
            source = "inferred: no explicit mandatory statement in JD"
            if count >= 3:
                weight, w_reason = 4, f"inferred: high frequency ({count} mentions)"
            elif count == 2:
                weight, w_reason = 3, "inferred: moderate emphasis (2 mentions)"
            else:
                weight, w_reason = 3, "inferred: default moderate priority"

        requirements.append({
            "requirement_id": f"req_{i:03d}",
            "name": canon.replace("_", " ").title() if "_" in canon else canon.title(),
            "category": _category_of(canon),
            "description": f"Demonstrated capability in {canon} as required by the JD",
            "importance_weight": weight,
            "mandatory": mandatory,
            "mandatory_source": source,
            "mandatory_explicit": bool(source.startswith("explicit")),
            "requirement_type": "technical_skill",
            "canonical_skill": canon,
            "aliases": [],
            "expected_evidence": ["project", "work_experience"],
            "source_page": 1,
            "source_text": source_text,
            "weight_inferred": not (mandatory and source.startswith("explicit")) and not in_pref,
            "weight_reason": w_reason,
            "mention_count": count,
        })
    # Dedupe equivalent canonical skills (normalization already applied).
    seen, deduped = set(), []
    for r in requirements:
        if r["canonical_skill"] in seen:
            continue
        seen.add(r["canonical_skill"])
        deduped.append(r)
    # Re-number after dedupe.
    for i, r in enumerate(deduped, start=1):
        r["requirement_id"] = f"req_{i:03d}"
    return deduped
