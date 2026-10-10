import json
import os
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
    Estimate professional experience from resume text.

    Signals:
      - explicit '5+ years of experience'
      - explicit durations attached to a role, including MONTHS/WEEKS
        (e.g. 'Python Developer Intern (2 months)', '6-month internship')
      - date ranges ('2019-2023', '2020-Present', 'Jan 2022 - Mar 2023')

    Only PROFESSIONAL experience counts (employment, internship, contract).
    Personal/academic projects are detected separately and never inflate
    professional experience.

    Returns years as a float. With detail=True returns (years, detail_dict).
    """
    import re
    low = (text or "").lower()

    years = 0.0
    for pattern in (r'(\d+)\+?\s*years?\s*of\s*experience',
                    r'(\d+)\+?\s*years?\s*experience',
                    r'experience\s*of\s*(\d+)\+?\s*years?',
                    r'(\d+)\+?\s*yrs?\s*experience',
                    r'over\s*(\d+)\s*years?'):
        for m in re.findall(pattern, low):
            val = int(m)
            if val < 50:
                years = max(years, float(val))

    roles = []
    # "2 months" / "6-month" / "1 year" attached to an employment/internship role
    role_ctx = r'[^.\n]{0,80}?(?:intern(?:ship)?|developer|engineer|analyst|manager|consultant|freelance|contract|full[\s-]?time|part[\s-]?time)'
    for m in re.finditer(role_ctx, low):
        window = low[m.start(): m.start() + 160]
        mo = re.search(r'(\d+)\s*(?:-|\s)?\s*(month|week|day|year)s?', window)
        if not mo:
            continue
        n, unit = int(mo.group(1)), mo.group(2)
        if unit == "month":
            yrs = n / 12.0
        elif unit == "week":
            yrs = n / 52.0
        elif unit == "day":
            yrs = n / 365.0
        else:
            yrs = float(n)
        if 0 < yrs < 50:
            roles.append({"role": m.group(0).strip()[:60], "years": round(yrs, 3),
                          "stated": mo.group(0).strip()})
            years = max(years, yrs)

    # Date-range inference (professional contexts only)
    date_years = 0.0
    try:
        try:
            from services.resume_parser import estimate_experience_from_dates
        except ImportError:
            from resume_parser import estimate_experience_from_dates
        date_years = float(estimate_experience_from_dates(text) or 0)
    except Exception:
        date_years = 0.0
    years = max(years, date_years)

    if not detail:
        return round(years, 2)

    return round(years, 2), {
        "years": round(years, 2),
        "explicit_mention_years": round(max(
            [float(int(m)) for p in (r'(\d+)\+?\s*years?\s*of\s*experience',
                                     r'(\d+)\+?\s*years?\s*experience')
             for m in re.findall(p, low) if int(m) < 50] or [0.0]), 2),
        "role_durations": roles,
        "date_range_years": round(date_years, 2),
        "professional": True,
        "note": "Professional roles and internships only; personal projects excluded.",
    }


def extract_education(text):
    """Detect education level from resume text."""
    text_lower = text.lower()
    if any(w in text_lower for w in ['phd', 'ph.d', 'doctorate']):
        return "PhD"
    elif any(w in text_lower for w in ['master', 'm.tech', 'm.sc', 'mba', 'mca']):
        return "Masters"
    elif any(w in text_lower for w in ['bachelor', 'b.tech', 'b.sc', 'b.e', 'b.com', 'undergraduate']):
        return "Bachelors"
    elif any(w in text_lower for w in ['diploma', 'certificate', '12th', 'higher secondary']):
        return "Diploma/Certificate"
    return "Not specified"


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
