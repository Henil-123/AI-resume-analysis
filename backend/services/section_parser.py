"""Section detection for resumes and JDs (configurable patterns + fallbacks).

Preserves the original text and page references; never fabricates sections.
"""
import re

RESUME_SECTION_PATTERNS = {
    "summary": [r"professional summary", r"^summary$", r"career objective", r"objective"],
    "skills": [r"technical skills", r"^skills$", r"core competencies", r"tech stack", r"technologies"],
    "experience": [r"work experience", r"professional experience", r"^experience$", r"employment history"],
    "projects": [r"^projects?$", r"personal projects", r"academic projects", r"key projects"],
    "education": [r"^education$", r"academic background", r"qualifications"],
    "certifications": [r"certifications?", r"licenses", r"credentials"],
    "achievements": [r"achievements?", r"accomplishments", r"awards"],
    "publications": [r"publications?", r"papers", r"research"],
}

JD_SECTION_PATTERNS = {
    "required": [r"required qualifications?", r"basic qualifications?", r"minimum qualifications?",
                 r"must have", r"what you.ll need", r"you have", r"key requirements?"],
    "preferred": [r"preferred qualifications?", r"nice to have", r"bonus", r"desired", r"good to have"],
    "responsibilities": [r"responsibilities", r"what you.ll do", r"you will", r"role overview", r"about the role"],
    "skills": [r"technical skills", r"required skills", r"tech stack"],
    "education": [r"education", r"degree", r"academic"],
    "experience": [r"experience", r"years of"],
    "behavioural": [r"soft skills", r"behavioural", r"behavioral", r"communication", r"leadership"],
}

_NON_EQUIVALENT = {
    # Related technologies that must NOT be treated as equivalent.
    "postgresql": {"spring data jpa", "spring_data_jpa"},
    "express": {"spring boot", "spring_boot"},
    "jwt": {"spring security"},
    "docker": {"kubernetes"},
    "javascript": {"typescript"},
    "java": {"advanced java"},
    "github": {"git"},
}


def _find_headers(lines, patterns):
    """Return {section: start_line_idx} for detected headers."""
    found = {}
    for i, line in enumerate(lines):
        s = re.sub(r"[^a-z ]", "", line.lower()).strip()
        if not s or len(s) > 60:
            continue
        for section, pats in patterns.items():
            if section in found:
                continue
            if any(re.search(p, s) for p in pats):
                found[section] = i
                break
    return found


def split_sections(raw_text, patterns):
    """Split raw text into {section: text}. Unknown text -> 'other'."""
    if not raw_text:
        return {"other": ""}
    lines = raw_text.split("\n")
    headers = _find_headers(lines, patterns)
    if not headers:
        return {"other": raw_text}
    ordered = sorted(headers.items(), key=lambda kv: kv[1])
    sections, n = {}, len(lines)
    for idx, (section, start) in enumerate(ordered):
        end = ordered[idx + 1][1] if idx + 1 < len(ordered) else n
        sections[section] = "\n".join(lines[start + 1:end]).strip()
    head = "\n".join(lines[:ordered[0][1]]).strip()
    if head:
        sections["header"] = head
    return sections


def parse_resume_sections(raw_text):
    """Resume sections with fallback: everything -> 'other' when no headers."""
    return split_sections(raw_text or "", RESUME_SECTION_PATTERNS)


def parse_jd_sections(raw_text):
    """JD sections with fallback: everything -> 'other' when no headers."""
    return split_sections(raw_text or "", JD_SECTION_PATTERNS)


def are_equivalent(skill_a, skill_b):
    """False when two skills are related but technically different."""
    a, b = (skill_a or "").lower(), (skill_b or "").lower()
    if a == b:
        return True
    for canonical, related in _NON_EQUIVALENT.items():
        group = {canonical} | {r.lower() for r in related}
        if a in group and b in group and a != b:
            return False
    return None  # unknown — caller decides via matching rules
