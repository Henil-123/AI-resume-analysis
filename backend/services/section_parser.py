"""Section detection for resumes and JDs (configurable patterns + fallbacks).

Preserves the original text and page references; never fabricates sections.
"""
import re

# Header lines are matched as PREFIXES with a word boundary, not as whole
# lines: "Skills: Java, React" must resolve to the skills section instead of
# being swallowed by the preceding section (which mislabelled skills-section
# and project text as "Education"). `\b` keeps "Experienced in X" from
# matching the "experience" header.
RESUME_SECTION_PATTERNS = {
    "summary": [r"^professional summary\b", r"^summary\b", r"career objective", r"^objective\b"],
    "skills": [r"technical skills", r"^skills\b", r"^key skills\b", r"^core competencies\b",
               r"core competencies", r"tech stack", r"^technologies\b",
               r"^technical proficiencies\b", r"^technical skills\b", r"^skills? ?& ?tools\b",
               r"^programming languages\b", r"^competencies\b"],
    "experience": [r"work experience", r"professional experience", r"^experience\b",
                   r"employment history", r"^employment\b",
                   # "INTERNSHIP EXPERIENCE" / "WORK EXPERIENCE" headings were
                   # previously unrecognised, so those blocks were absorbed by
                   # the preceding section (skills text landed in Education).
                   r"^internship experience\b", r"^work experience\b",
                   r"^internships?\b", r"^work history\b", r"^professional background\b"],
    "projects": [r"^projects?\b", r"personal projects", r"academic projects",
                 r"key projects", r"^project work\b", r"^academic projects?\b"],
    "education": [r"^education\b", r"academic background", r"^qualifications\b",
                  r"^educational (?:qualifications|background)\b", r"^academics\b"],
    "certifications": [r"^certifications?\b", r"^licenses\b", r"^credentials\b",
                       r"^courses? (?:done|certified)\b"],
    "achievements": [r"^achievements?\b", r"accomplishments", r"^awards?\b",
                     r"^achievements and awards\b"],
    "publications": [r"^publications?\b", r"^papers\b", r"^research\b"],
}

# Section precedence when a line matches more than one pattern family.
_SECTION_PRIORITY = ["skills", "experience", "projects", "education",
                     "certifications", "achievements", "publications", "summary"]

JD_SECTION_PATTERNS = {
    # NOTE: bare "required"/"preferred" are required here. Without them a JD
    # heading like "Required Skills:" is not detected as a required section,
    # which previously collapsed into the no-sections fallback and marked
    # EVERY skill mandatory (including things listed under "Preferred").
    "required": [r"required qualifications?", r"basic qualifications?", r"minimum qualifications?",
                 r"must have", r"must-have", r"what you.ll need", r"you have",
                 r"key requirements?", r"skills? required", r"required skills?",
                 r"^required\b", r"^requirements?\b", r"^minimum\b", r"^basic qualifications?\b",
                 r"^must haves?\b", r"essential\b", r"mandatory\b"],
    "preferred": [r"preferred qualifications?", r"nice to have", r"nice-to-have",
                  r"bonus", r"desired", r"good to have", r"^preferred\b",
                  r"^bonus\b", r"^pluses?\b", r"^desirable\b"],
    "responsibilities": [r"responsibilities", r"what you.ll do", r"you will", r"role overview", r"about the role"],
    "skills": [r"technical skills", r"tech stack", r"^skills\b", r"^technologies\b"],
    "education": [r"^education\b", r"degree", r"academic"],
    "experience": [r"^experience\b", r"years of"],
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
    """Return {section: start_line_idx} for detected headers.

    Scans top-down so each line is attributed to the FIRST matching section.
    ``_SECTION_PRIORITY`` disambiguates lines matching several families
    (e.g. "Technical Skills" matches both skills and education).
    """
    found = {}
    inline = {}
    order = [s for s in _SECTION_PRIORITY if s in patterns]
    order += [s for s in patterns if s not in order]
    for i, line in enumerate(lines):
        raw = line.strip()
        s = re.sub(r"[^a-z ]", "", line.lower()).strip()
        if not s or len(s) > 60:
            continue
        for section in order:
            if section in found:
                continue
            for p in patterns[section]:
                m = re.match(p, s)
                if not m:
                    continue
                found[section] = i
                # Text after the header token on the same line is real section
                # content ("Skills: Java, React" -> "Java, React"). `s` has
                # had punctuation stripped, so slice `raw` by the header's
                # character length instead of searching for a stripped token.
                m_raw = re.match(p, raw.lower().strip())
                if m_raw and m_raw.end() < len(raw):
                    tail = raw[m_raw.end():].lstrip(" :-\t").strip()
                    if tail:
                        inline[section] = tail[:400]
                break
            if section in found:
                break
    return found, inline


def _line_offsets(raw_text):
    """Character offset at which each line starts (for accurate page mapping)."""
    offsets, pos = [], 0
    for line in (raw_text or "").split("\n"):
        offsets.append(pos)
        pos += len(line) + 1
    return offsets


def split_sections_indexed(raw_text, patterns):
    """Like split_sections, but also returns {section: char_start_offset}.

    The offset is the position of the section's HEADER line, so a passage can
    be mapped back to the page it actually came from. Previously the offset was
    found via `raw.find(body[:60])`, which returns the FIRST occurrence of that
    text anywhere in the document and silently attributed evidence to the wrong
    page (and, when sections repeated, the wrong section).
    """
    if not raw_text:
        return {"other": ""}, {}
    lines = raw_text.split("\n")
    headers, inline = _find_headers(lines, patterns)
    line_off = _line_offsets(raw_text)
    if not headers:
        return {"other": raw_text}, {"other": 0}
    ordered = sorted(headers.items(), key=lambda kv: kv[1])
    sections, offsets, n = {}, {}, len(lines)
    for idx, (section, start) in enumerate(ordered):
        end = ordered[idx + 1][1] if idx + 1 < len(ordered) else n
        body = "\n".join(lines[start + 1:end]).strip()
        tail = inline.get(section, "")
        if tail:
            body = (tail + "\n" + body).strip()
        sections[section] = body
        offsets[section] = line_off[start]
    head = "\n".join(lines[:ordered[0][1]]).strip()
    if head:
        sections["header"] = head
        offsets["header"] = 0
    return sections, offsets


def split_sections(raw_text, patterns):
    """Split raw text into {section: text}. Unknown text -> 'other'.

    Each section value starts AFTER its header line, so header text itself is
    never emitted as section body.
    """
    return split_sections_indexed(raw_text, patterns)[0]


def parse_resume_sections(raw_text):
    """Resume sections with fallback: everything -> 'other' when no headers."""
    return split_sections(raw_text or "", RESUME_SECTION_PATTERNS)


def parse_resume_sections_indexed(raw_text):
    """Resume sections + their character offsets (for page provenance)."""
    return split_sections_indexed(raw_text or "", RESUME_SECTION_PATTERNS)


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
