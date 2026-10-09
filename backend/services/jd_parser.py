"""Job Description understanding: parse JD file/text into structured requirements."""
import os
import re

try:
    from services.resume_parser import extract_text, clean_text
    from services.skill_extractor import (
        extract_required_skills_from_jd,
        extract_skills_with_categories,
        extract_experience_years,
        extract_education,
    )
except ImportError:
    from resume_parser import extract_text, clean_text
    from skill_extractor import (
        extract_required_skills_from_jd,
        extract_skills_with_categories,
        extract_experience_years,
        extract_education,
    )


MUST_HAVE_HEADERS = (
    "must have", "must-have", "required", "requirement", "basic qualification",
    "minimum qualification", "what you'll need", "what you need", "you have",
    "you bring", "key requirement",
)
NICE_HEADERS = (
    "nice to have", "nice-to-have", "preferred", "bonus", "plus ",
    "good to have", "desired", "additional",
)


def _header_kind(line):
    """Classify a section header line as must / nice / other."""
    low = re.sub(r'[^a-z ]', '', line.lower()).strip()
    if not low or len(low) > 60:
        return None
    if any(k in low for k in NICE_HEADERS):
        return "nice"
    if any(k in low for k in MUST_HAVE_HEADERS):
        return "must"
    if low in ("qualifications", "skills", "experience", "responsibilities",
               "about the role", "job summary", "overview"):
        return "must"
    return None


def split_jd_sections(raw_text):
    """Split JD raw text into (must_text, nice_text).

    Lines following a must-have/requirements header go to must_text,
    lines under nice-to-have/preferred headers go to nice_text.
    """
    must_lines, nice_lines = [], []
    if not raw_text:
        return "", ""
    current = None
    for line in raw_text.split("\n"):
        s = line.strip().strip("-•*·>#: ")
        kind = _header_kind(s)
        if kind in ("must", "nice"):
            current = kind
            continue
        if not s:
            continue
        if current == "must":
            must_lines.append(line)
        elif current == "nice":
            nice_lines.append(line)
    return "\n".join(must_lines), "\n".join(nice_lines)


def build_skill_weights(jd_text, must_have_skills, nice_to_have_skills,
                        skill_counts, must_boost=2.0, nice_factor=0.7):
    """Weight each JD skill: frequency emphasis × section factor.

    weight = (1 + 0.25 × extra_mentions) × (must_boost | nice_factor | 1.0)
    """
    must_set, nice_set = set(must_have_skills), set(nice_to_have_skills)
    weights = {}
    for skill, count in skill_counts.items():
        w = 1.0 + 0.25 * max(0, count - 1)
        if skill in must_set:
            w *= must_boost
        elif skill in nice_set:
            w *= nice_factor
        weights[skill] = round(w, 3)
    return weights


def extract_job_title(raw_text):
    """Heuristic JD title: explicit 'Job Title:'/'Role:' line, else first meaningful line."""
    if not raw_text:
        return ""
    # Explicit label first
    m = re.search(r'(?im)^\s*(job\s*title|role|position)\s*[:\-]\s*(.+)$', raw_text)
    if m:
        title = m.group(2).strip().split("\n")[0].strip()
        if 2 < len(title) < 80:
            return title.title()
    # Fallback: first substantial line that isn't a header/greeting
    skip = ("job description", "about the", "company", "overview",
            "we are", "join us", "responsibilities", "requirements",
            "qualifications", "what you", "who you", "job summary")
    for line in raw_text.strip().split("\n")[:12]:
        line = line.strip().strip("-•*#: ")
        low = line.lower()
        if len(line) < 4 or len(line) > 80:
            continue
        if any(s in low for s in skip):
            continue
        if re.search(r'\d', line) and len(line.split()) > 8:
            continue
        # Prefer short title-like lines
        if len(line.split()) <= 8:
            return line.title()
    return ""


def extract_min_experience(jd_text):
    """JD-specific experience: 'minimum 5 years', '3+ years', '5-8 years'."""
    if not jd_text:
        return 0
    text = jd_text.lower()
    vals = []
    for pat in [
        r'minimum\s*(?:of\s*)?(\d+)\+?\s*(?:years?|yrs?)',
        r'at\s*least\s*(\d+)\+?\s*(?:years?|yrs?)',
        r'(\d+)\+\s*(?:years?|yrs?)\s*(?:of\s*)?(?:experience|exp)',
        r'(\d+)\s*-\s*(\d+)\s*(?:years?|yrs?)\s*(?:of\s*)?(?:experience|exp)?',
        r'(\d+)\s*(?:years?|yrs?)\s*(?:of\s*)?(?:experience|exp|minimum)',
    ]:
        for m in re.finditer(pat, text):
            try:
                nums = [int(g) for g in m.groups() if g and g.isdigit()]
                if nums and max(nums) < 50:
                    # for ranges like 5-8, take the lower bound (minimum bar)
                    vals.append(min(nums))
            except Exception:
                pass
    if vals:
        return max(vals)
    # Fallback to generic resume-style extractor
    try:
        return extract_experience_years(jd_text)
    except Exception:
        return 0


def extract_key_points(raw_text, keywords=("responsib", "requirement", "qualific", "must have",
                                            "nice to have", "what you'll do", "you will"),
                       limit=8):
    """Pull bullet-ish requirement lines so the UI can show 'what the JD wants'."""
    if not raw_text:
        return []
    points = []
    for line in raw_text.split("\n"):
        s = line.strip().strip("-•*·> ").strip()
        if len(s) < 20 or len(s) > 300:
            continue
        low = s.lower()
        # Keep lines that look like requirements, or bullets under those sections
        if line.strip()[:1] in "-•*·" or any(k in low for k in
                                             ("experience", "proficiency", "strong",
                                              "knowledge", "ability", "bachelor",
                                              "master", "degree", "familiar")):
            points.append(s)
        if len(points) >= limit * 2:
            break
    return points[:limit]


def summarize_jd(job_title, skills, min_exp, education, word_count):
    """2-3 sentence plain-English understanding of the JD."""
    role = job_title or "This role"
    parts = []
    if skills:
        top = ", ".join(skills[:6])
        parts.append(f"{role} requires {len(skills)} key skill(s), including {top}.")
    else:
        parts.append(f"{role} lists no taxonomy-matched skills — semantic matching will carry the score.")
    if min_exp > 0:
        parts.append(f"Minimum experience: {min_exp} year(s).")
    else:
        parts.append("No explicit experience bar detected.")
    parts.append(f"Education signal: {education} ({word_count} words parsed).")
    return " ".join(parts)


def parse_jd_text(jd_text):
    """Parse raw JD text into structured understanding. Returns dict."""
    raw = jd_text or ""
    cleaned = clean_text(raw)
    skills = extract_required_skills_from_jd(raw)
    try:
        skill_counts = extract_required_skills_from_jd(raw, with_counts=True)
    except TypeError:
        skill_counts = {s: 1 for s in skills}
    must_text, nice_text = split_jd_sections(raw)
    must_have = extract_required_skills_from_jd(must_text) if must_text else []
    nice_to_have = extract_required_skills_from_jd(nice_text) if nice_text else []
    # If no explicit sections found, every skill counts as core (must).
    if not must_text and not nice_text:
        must_have = list(skills)
    skill_weights = build_skill_weights(raw, must_have, nice_to_have, skill_counts)
    try:
        by_cat = extract_skills_with_categories(raw)
    except Exception:
        by_cat = {}
    min_exp = extract_min_experience(raw)
    education = extract_education(raw)
    title = extract_job_title(raw)
    points = extract_key_points(raw)
    return {
        "job_title": title,
        "job_description": raw[:10000],
        "clean_text": cleaned[:10000],
        "word_count": len(cleaned.split()),
        "required_skills": skills,
        "skill_weights": skill_weights,
        "must_have_skills": must_have,
        "nice_to_have_skills": nice_to_have,
        "skills_by_category": by_cat,
        "min_experience": min_exp,
        "education_required": education,
        "key_points": points,
        "summary": summarize_jd(title, skills, min_exp, education, len(cleaned.split())),
    }


def parse_jd_file(file_path):
    """Parse a JD file (pdf/docx/txt) into structured understanding."""
    raw = extract_text(file_path)
    result = parse_jd_text(raw)
    result["jd_file"] = os.path.basename(file_path)
    return result
