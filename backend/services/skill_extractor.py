import json
import os
import re
import spacy
from spacy.matcher import PhraseMatcher

try:
    from services.skill_aliases import normalize_skill, normalize_skill_list
except ImportError:
    from skill_aliases import normalize_skill, normalize_skill_list

# ── Load spaCy model (E050-proof: download, else blank pipeline) ────
def _load_nlp():
    """Load en_core_web_sm, downloading it on first use if missing.

    Last-resort fallback is a blank English pipeline: the skill
    PhraseMatcher only needs tokenization, so skill extraction keeps
    working even with no model installed. Returns (nlp, model_name).
    """
    import spacy
    try:
        return spacy.load("en_core_web_sm"), "en_core_web_sm"
    except OSError:
        pass
    try:
        import subprocess
        import sys
        subprocess.check_call(
            [sys.executable, "-m", "spacy", "download", "en_core_web_sm"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return spacy.load("en_core_web_sm"), "en_core_web_sm"
    except Exception:
        pass
    print("WARNING: 'en_core_web_sm' unavailable — using blank 'en' pipeline. "
          "Skill matching still works; NER-based features are degraded.")
    return spacy.blank("en"), "blank-en (fallback)"


nlp, SPACY_MODEL_NAME = _load_nlp()

# ── Load skills dataset ───────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILLS_PATH = os.path.join(BASE_DIR, "data", "skills", "skills_dataset.json")

with open(SKILLS_PATH, "r") as f:
    skills_data = json.load(f)

# Build flat list and category map
all_skills = []
skill_category_map = {}

for category, skills in skills_data.items():
    for skill in skills:
        skill_lower = skill.lower()
        all_skills.append(skill_lower)
        skill_category_map[skill_lower] = category

# Build PhraseMatcher
matcher = PhraseMatcher(nlp.vocab, attr="LOWER")
patterns = [nlp.make_doc(skill) for skill in all_skills]
matcher.add("SKILLS", patterns)

# Also match known aliases so "k8s" / "js" / "postgres" are detected
try:
    from services.skill_aliases import ALIASES
except ImportError:
    from skill_aliases import ALIASES
try:
    alias_patterns = [nlp.make_doc(alias) for alias in ALIASES.keys()]
    if alias_patterns:
        matcher.add("SKILL_ALIASES", alias_patterns)
except Exception:
    pass


def extract_skills(text):
    """
    Extract skills from resume text.
    Returns: list of found skill strings (canonical, alias-normalized).
    """
    if not text:
        return []
    doc = nlp(text[:100000])  # spaCy limit safety
    matches = matcher(doc)
    found = set()
    for _, start, end in matches:
        skill = doc[start:end].text.lower()
        found.add(normalize_skill(skill))
    return sorted(list(found))


def extract_skills_with_categories(text):
    """
    Extract skills grouped by category.
    Returns: dict of {category: [skills]}
    """
    skills = extract_skills(text)
    categorized = {}
    for skill in skills:
        cat = skill_category_map.get(skill, "other")
        categorized.setdefault(cat, []).append(skill)
    return categorized


def extract_experience_years(text, detail=False):
    """
    Professional experience in years (section-aware).

    Delegates to experience_extractor, which only counts EMPLOYMENT sections,
    merges overlapping date ranges, honours month precision, and reports
    project/education periods separately. The previous implementation scanned
    the whole text for any year pair and took the maximum, which counted a
    2021-2025 degree and 2024-2025 project as professional employment and
    inflated a two-month internship to four years.

    Returns a float, or (years, detail_dict) when detail=True.
    """
    return extract_experience_years_from_profile(text, detail=detail)


def extract_education(text):
    """Detect education level from resume text.

    Word-boundary aware: "b.e" previously matched inside unrelated words, and
    common campus-JD spellings ("BE/BTech", "B Tech", "B.E.") are now covered
    so a real degree requirement is not reported as "Not specified".
    """
    t = (text or "").lower()
    if re.search(r"\b(ph\.?d|doctorate)\b", t):
        return "PhD"
    if re.search(r"\b(master'?s?|m\.?tech|m\.?sc|mba|mca|ms\b|post\s*graduate)", t):
        return "Masters"
    if re.search(r"\b(bachelor'?s?|b\.?\s?tech|b\.?\s?sc|b\.?\s?e\b|b\.?\s?com|"
                 r"b\.?\s?eng|undergraduate|b\.?e\b/?\s?b\.?tech|be\s*/\s*btech)", t):
        return "Bachelors"
    if re.search(r"\b(diploma|certificate|12th|higher secondary|intermediate|10th)\b", t):
        return "Diploma/Certificate"
    return "Not specified"


def extract_experience_years_from_profile(raw_text, detail=False):
    """Professional experience only (section-aware). See experience_extractor."""
    try:
        try:
            from services.experience_extractor import extract_experience_profile
        except ImportError:
            from experience_extractor import extract_experience_profile
        prof = extract_experience_profile(raw_text or "")
    except Exception as exc:
        prof = {"professional_years": 0.0, "confidence": "none",
                "basis": f"extraction failed: {exc}",
                "note": "Experience could not be "
                "verified; treated as unknown, not zero.", "evidence": [],
                "unverifiable": [], "roles": [], "project_years": 0.0,
                "project_periods": [], "education_years": 0.0,
                "education_periods": [], "explicit_durations": [],
                "stated_years_claims": [], "employment_section_found": False,
                "sections_seen": []}
    if detail:
        return prof["professional_years"], prof
    return prof["professional_years"]


def extract_required_skills_from_jd(job_description, max_skills=20, with_counts=False):
    """
    Auto-derive required skills from a free-form job description.
    Uses the same PhraseMatcher taxonomy so JD skills and resume
    skills share one canonical vocabulary (alias-normalized).

    Returns: list of canonical skill strings (ordered by frequency),
    or {skill: mention_count} dict when with_counts=True.
    """
    if not job_description:
        return {} if with_counts else []
    from collections import Counter
    doc = nlp(job_description[:20000].lower())
    matches = matcher(doc)
    counter = Counter()
    for _, start, end in matches:
        skill = normalize_skill(doc[start:end].text.lower())
        counter[skill] += 1
    # Most-frequent first = rough proxy for importance in JD
    top = counter.most_common(max_skills)
    if with_counts:
        return dict(top)
    return [s for s, _ in top]
