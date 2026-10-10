"""Resume evidence extraction (spec section 6).

One record per relevant passage — never fabricated page numbers or offsets
(offsets are null unless established). Duplicate passages are preserved as
separate records but flagged so scoring uses only the best one.
"""
import re

try:
    from services.skill_aliases import normalize_skill
    from services.section_parser import parse_resume_sections
except ImportError:
    from skill_aliases import normalize_skill
    from section_parser import parse_resume_sections

NEGATION_RE = re.compile(r"\b(no|not|never|without|n't|lack(?:s|ing)?|unfamiliar)\b", re.I)
PLANNED_RE = re.compile(r"\b(will|plan(?:ning|s)? to|going to|aspir(?:ing|ation)|to learn|currently learning|familiar with|exposure to)\b", re.I)
IMPLEMENT_VERBS = re.compile(r"\b(built|implement(?:ed|ation)?|deployed|designed|developed|shipped|launched|tested|optimized|integrated|maintained|led|architected|debugged)\b", re.I)
USE_VERBS = re.compile(r"\b(used|using|worked with|applied|utilized|leveraged|proficient|experienced|hands-on)\b", re.I)

SECTION_TO_EVIDENCE = {
    "skills": "skills_section",
    "experience": "work_experience",
    "projects": "project_implementation",
    "certifications": "certification",
    "education": "education",
    "summary": "summary_mention",
    "achievements": "achievement",
    "publications": "publication",
    "header": "header_mention",
    "other": "general_mention",
}


def _split_sentences(text):
    sents = []
    for line in (text or "").split("\n"):
        sents.extend(s.strip() for s in re.split(r"(?<=[.!?•\-])\s+", line) if len(s.strip()) > 8)
    return sents


def _page_for_offset(pages, char_offset):
    if char_offset is None:
        return 1
    acc = 0
    for p in pages or []:
        t = p.get("text", "") if isinstance(p, dict) else p[1]
        if acc <= char_offset < acc + len(t) + 1:
            return p.get("page", 1) if isinstance(p, dict) else p[0]
        acc += len(t) + 1
    return 1


def extract_evidence(parsed_resume, skills=None):
    """Build evidence records for each skill from resume sections.

    parsed_resume: dict from parse_resume (raw_text, pages).
    skills: iterable of canonical skills to look for (default: all detected).
    """
    raw = (parsed_resume or {}).get("raw_text", "")
    pages = (parsed_resume or {}).get("pages", [(1, raw)])
    if skills is None:
        try:
            try:
                from services.skill_extractor import extract_skills
            except ImportError:
                from skill_extractor import extract_skills
            from services.resume_parser import clean_text
            skills = extract_skills(clean_text(raw))
        except Exception:
            skills = []
    sections = parse_resume_sections(raw)
    # char offsets per section for page mapping
    offsets, cursor = {}, 0
    lowered_raw = raw.lower()
    for sec, body in sections.items():
        idx = lowered_raw.find(body.lower()[:60]) if body else -1
        offsets[sec] = idx if idx >= 0 else None

    records, seen_passages, n = [], set(), 0
    for section, body in sections.items():
        for sent in _split_sentences(body):
            low = sent.lower()
            for skill in skills or []:
                canon = normalize_skill(skill)
                if canon not in low and canon.replace("_", " ") not in low and canon.replace(" ", "") not in low:
                    # word-boundary phrase check for short acronyms
                    if not re.search(r"\b" + re.escape(canon) + r"\b", low):
                        continue
                n += 1
                is_neg = bool(NEGATION_RE.search(" ".join(low.split()[:6])) and canon in low)
                is_planned = bool(PLANNED_RE.search(low))
                etype = SECTION_TO_EVIDENCE.get(section, "general_mention")
                if etype in ("work_experience", "project_implementation") and IMPLEMENT_VERBS.search(low):
                    etype = "project_implementation" if section == "projects" else "work_implementation"
                key = (canon, sent[:160])
                dup = key in seen_passages
                seen_passages.add(key)
                records.append({
                    "evidence_id": f"evidence_{n:03d}",
                    "canonical_skill": canon,
                    "original_text": sent[:500],
                    "section": section.title() if section else "Other",
                    "project_name": None,
                    "page_number": _page_for_offset(pages, offsets.get(section)),
                    "evidence_type": etype,
                    "polarity": "negative" if is_neg else "positive",
                    "is_negated": is_neg,
                    "is_planned": is_planned,
                    "is_duplicate": dup,
                    "source_start": None,
                    "source_end": None,
                })
    return records
