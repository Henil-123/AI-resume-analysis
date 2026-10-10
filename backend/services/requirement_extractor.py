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
    """Convert JD text into deduplicated structured requirements."""
    jd_text = jd_text or ""
    meta = jd_meta or {}
    sections = parse_jd_sections(jd_text)
    try:
        counts = extract_required_skills_from_jd(jd_text, with_counts=True)
    except TypeError:
        counts = {s: 1 for s in extract_required_skills_from_jd(jd_text)}
    req_section_skills = set(extract_required_skills_from_jd(sections.get("required", "")) or [])
    pref_section_skills = set(extract_required_skills_from_jd(sections.get("preferred", "")) or [])

    requirements = []
    for i, (skill, count) in enumerate(
            sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])), start=1):
        canon = normalize_skill(skill)
        # Mandatory reasoning: explicit marker > required-section placement >
        # no-sections fallback (all core) > frequency/role-title emphasis.
        in_must = canon in {normalize_skill(s) for s in req_section_skills}
        in_pref = canon in {normalize_skill(s) for s in pref_section_skills}
        explicit_must = in_must
        if not sections.get("required") and not sections.get("preferred"):
            mandatory, source = True, "inferred: no JD sections, all skills treated as core"
            weight, w_reason = 4, "inferred: core skill, no section signal"
        elif explicit_must:
            mandatory, source = True, "explicit: listed under required/must-have section"
            weight, w_reason = 5, "explicit: required section"
        elif in_pref:
            mandatory, source = False, "explicit: listed under preferred/nice-to-have section"
            weight, w_reason = 2, "explicit: preferred section"
        elif count >= 3:
            mandatory, source = False, f"inferred: mentioned {count}x (emphasis), not marked mandatory"
            weight, w_reason = 4, f"inferred: high frequency ({count} mentions)"
        elif count == 2:
            mandatory, source = False, "inferred: repeated mention, non-mandatory"
            weight, w_reason = 3, "inferred: moderate emphasis"
        else:
            mandatory, source = False, "inferred: single mention, importance unclear"
            weight, w_reason = 3, "inferred: default moderate"
        # source sentence: first line containing the skill
        source_text, source_page = "", 1
        for line in jd_text.split("\n"):
            if canon in line.lower():
                source_text = line.strip()[:300]
                break
        requirements.append({
            "requirement_id": f"req_{i:03d}",
            "name": canon.replace("_", " ").title() if "_" in canon else canon.title(),
            "category": _category_of(canon),
            "description": f"Demonstrated capability in {canon} as required by the JD",
            "importance_weight": weight,
            "mandatory": mandatory,
            "mandatory_source": source,
            "requirement_type": "technical_skill",
            "canonical_skill": canon,
            "aliases": [],
            "expected_evidence": ["project", "work_experience"],
            "source_page": source_page,
            "source_text": source_text,
            "weight_inferred": not explicit_must and not in_pref,
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
