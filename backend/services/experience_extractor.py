"""Professional vs project experience extraction (spec section 6).

The previous implementation scanned the ENTIRE resume text for any year pair
("2021 - 2025", "2024 - 2025") and took the maximum span. That counted
university enrolment and personal-project dates as professional employment and
turned a two-month internship into "4 years".

This module is deliberately conservative:

  * Only sections that are recognisably EMPLOYMENT contribute professional
    experience. Project and education dates are reported separately.
  * Explicit durations ("2 months") and explicit statements ("3+ years of
    experience") are tracked as their own signals.
  * Date ranges are merged as month intervals, so overlapping or nested roles
    are never double counted.
  * A start date with no end date and no "present/current" marker is NOT
    completed with an invented end date; it is reported as unverifiable.
  * When nothing can be verified, the result is an explicit unknown with the
    evidence that was inspected — never a fabricated number.
"""
import re

try:
    from services.section_parser import parse_resume_sections_indexed
except ImportError:
    from section_parser import parse_resume_sections_indexed

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}

_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_YR = r"(?:19|20)\d{2}"
_OPEN = r"(?:present|current|currently|now|today|ongoing|to date|till date|until date|"
_OPEN += r"to present|till now)"
_SEP = r"\s*(?:-|--|–|—|to|until|till)\s*"

# Ordered most-specific first; the separator is optional because cleaned text
# drops punctuation ("2022-Present" becomes "2022 present").
# A unified range pattern. The separator is REQUIRED for a concrete end date
# ("June 2025 August 2025" would otherwise be ambiguous prose) but OPTIONAL
# before an open marker, because cleaned text drops the hyphen and turns
# "2022-Present" into "2022 present".
_DATE_TOKEN = r"(?:%s\s+%s|%s)" % (_MON, _YR, _YR)
_RANGE_RE = re.compile(
    r"(%s)\s*(?:(?:-|–|—|to|until|till)\s*((?:%s\s+%s|%s|%s))|(%s)\b)"
    % (_DATE_TOKEN, _MON, _YR, _YR, _OPEN, _OPEN),
    re.I)

_DURATION_RE = re.compile(
    r"\b(\d{1,2})\s*(?:\-\s*)?\s*(month|week|year|day)s?\b", re.I)
_EXPLICIT_YEARS_RE = re.compile(
    r"\b(\d{1,2})\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:experience|exp\b)", re.I)

_ROLE_HINT_RE = re.compile(
    r"\b(intern|internship|trainee|apprentice|developer|engineer|analyst|"
    r"manager|consultant|associate|specialist|lead|architect|associate|"
    r"freelance|contract|officer|teacher|professor|assistant)\b", re.I)

# Words that mark a line as a role title rather than a sentence.
_TITLE_STOP = re.compile(r"[.:;,]$")


def _y_to_idx(year, month=1):
    return int(year) * 12 + (int(month) - 1)


def _today_index(today=None):
    import datetime
    d = today or datetime.date.today()
    return _y_to_idx(d.year, d.month)


def _mk(text):
    m = re.match(r"([a-z]{3,})", text.lower())
    return MONTHS.get(m.group(1)[:3]) if m else None


def _looks_like_open(tok):
    return bool(re.match(_OPEN + r"$", tok.strip(), re.I))


def _parse_date_token(tok):
    """'June 2025' -> month index, '2025' -> January index, else None."""
    tok = (tok or "").strip()
    if _looks_like_open(tok):
        return _today_index() + 1, True
    m = re.match(r"^(%s)\s+(%s)$" % (_MON, _YR), tok, re.I)
    if m:
        return _y_to_idx(m.group(2), _mk(m.group(1))), False
    m = re.match(r"^(%s)$" % _YR, tok)
    if m:
        # Year-only range: start at January, end runs to December.
        return _y_to_idx(m.group(1), 1), False
    m = re.match(r"^(%s)\s*(\d{4})?$" % _MON, tok, re.I)
    if m and m.group(1):
        if not m.group(2):
            return None, False  # bare month, no year: ambiguous
        return _y_to_idx(m.group(2), _mk(m.group(1))), False
    return None, False


def _parse_ranges(text):
    """Yield month-intervals (start_idx, end_idx) found in a text block."""
    out = []
    for m in _RANGE_RE.finditer(text or ""):
        start = m.group(1).strip()
        # Exactly one of the two end alternatives participates in a match.
        end = (m.group(2) or m.group(3) or "").strip()
        s_idx, _ = _parse_date_token(start)
        e_idx, ongoing = _parse_date_token(end)
        if s_idx is None or e_idx is None:
            continue
        # A year-only end token means December of that year.
        if re.match(r"^(%s)$" % _YR, end) and not ongoing:
            e_idx = _y_to_idx(end, 12) + 1
        if e_idx <= s_idx:
            continue
        out.append({"start_idx": s_idx, "end_idx": e_idx,
                    "raw": m.group(0).strip(), "ongoing": ongoing,
                    "start_label": start, "end_label": end})
    return out


def _merge_intervals(intervals):
    """Union month-intervals so overlapping/adjacent roles count once."""
    if not intervals:
        return []
    spans = sorted((i["start_idx"], i["end_idx"]) for i in intervals)
    merged = [list(spans[0])]
    for s, e in spans[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(a, b) for a, b in merged]


def _months(spans):
    return sum(b - a for a, b in spans)


def _lines_of(block):
    return [l.strip() for l in (block or "").split("\n") if l.strip()]


def _entry_titles(block):
    """Best-effort role titles paired with their date ranges."""
    entries = []
    lines = _lines_of(block)
    for i, line in enumerate(lines):
        ranges = _parse_ranges(line)
        if not ranges:
            continue
        # nearest preceding line that looks like a role title
        title = ""
        for j in range(i - 1, max(-1, i - 4), -1):
            cand = lines[j]
            if _ROLE_HINT_RE.search(cand) and not _parse_ranges(cand):
                title = cand[:80]
                break
        if not title:
            title = line[:80]
        entries.append({"title": title, "ranges": ranges,
                        "raw": line[:160], "line_index": i})
    return entries


def _explicit_durations(block):
    out = []
    for line in _lines_of(block):
        for m in _DURATION_RE.finditer(line):
            n, unit = int(m.group(1)), m.group(2).lower()
            if unit == "month":
                yrs = n / 12.0
            elif unit == "week":
                yrs = n / 52.0
            elif unit == "year":
                yrs = float(n)
            else:
                yrs = n / 365.0
            if 0 < yrs < 50:
                out.append({"years": round(yrs, 3), "stated": m.group(0).strip(),
                            "context": line[:120]})
    return out


def _explicit_years(block):
    out = []
    for m in _EXPLICIT_YEARS_RE.finditer(block or ""):
        v = int(m.group(1))
        if 0 < v < 50:
            out.append({"years": float(v), "stated": m.group(0).strip(),
                        "context": (block[max(0, m.start() - 60):m.end() + 40] or "")[:120]})
    return out


def _unverifiable_starts(block):
    """Start dates with no resolvable end (never completed with a guess).

    Covers both "2023 - " (dangling separator) and "Started 2023" (no end
    token at all). Each detected start is reported with its reason so the
    reviewer can supply the missing information.
    """
    out = []
    start_only = re.compile(r"(%s\s+%s|%s)" % (_MON, _YR, _YR), re.I)
    for line in _lines_of(block):
        if not _parse_ranges(line):
            for m in start_only.finditer(line):
                out.append({
                    "raw": line[:160],
                    "found": m.group(1).strip(),
                    "reason": "start date found but no end date or 'present' "
                              "marker; end date not invented",
                })
    return out


def extract_experience_profile(raw_text, today=None):
    """Professional experience with full provenance.

    Returns professional years, the merged employment intervals, separate
    project/education periods, the signals used, and an explicit confidence
    level. Never fabricates an end date or a duration.
    """
    raw = raw_text or ""
    sections, offsets = parse_resume_sections_indexed(raw) if raw else ({}, {})

    emp_block = sections.get("experience", "") or ""
    proj_block = sections.get("projects", "") or ""
    edu_block = sections.get("education", "") or ""
    has_emp_section = bool(emp_block.strip())

    entries = _entry_titles(emp_block) if has_emp_section else []
    durations = _explicit_durations(emp_block) if has_emp_section else []
    exp_years = _explicit_years(emp_block) if has_emp_section else []

    if not has_emp_section:
        # Without a recognisable employment section we cannot attribute a bare
        # date range to employment: it may be a degree or a personal project.
        # Only unambiguous explicit statements are accepted.
        durations = _explicit_durations(raw)
        exp_years = _explicit_years(raw)
        entries = []

    intervals = [r for e in entries for r in e["ranges"]]
    merged = _merge_intervals(intervals)
    date_months = _months(merged)

    dur_months = 0
    for d in durations:
        dur_months = max(dur_months, round(d["years"] * 12))

    stated_years = max([e["years"] for e in exp_years], default=0.0)

    prof_months = max(date_months, dur_months)
    if stated_years > 0:
        prof_months = max(prof_months, round(stated_years * 12))

    prof_years = round(prof_months / 12.0, 2)

    project_entries = _entry_titles(proj_block)
    project_intervals = [r for e in project_entries for r in e["ranges"]]
    project_months = _months(_merge_intervals(project_intervals))
    edu_entries = _entry_titles(edu_block)
    edu_intervals = [r for e in edu_entries for r in e["ranges"]]
    edu_months = _months(_merge_intervals(edu_intervals))

    basis = []
    if date_months:
        basis.append("employment date ranges (overlap-merged)")
    if dur_months and not date_months:
        basis.append("explicit duration stated in employment section")
    if stated_years:
        basis.append("explicit 'years of experience' statement")
    if not basis:
        basis.append("no verifiable professional-experience signal")

    if prof_years <= 0:
        confidence = "none"
        note = ("No professional-experience duration could be verified. "
                "This is an unknown, not a claim of zero experience.")
    elif date_months and dur_months and abs(date_months - dur_months) > 2:
        confidence = "medium"
        note = ("Employment dates and the stated duration disagree; the larger "
                "verified value was used.")
    elif date_months or stated_years:
        confidence = "high"
        note = "Derived from dated employment entries in the resume."
    else:
        confidence = "low"
        note = "Derived from an explicitly stated duration only (no dates)."

    evidence = []
    for e in entries:
        evidence.append(f"{e['title']} | {'; '.join(r['raw'] for r in e['ranges'])}"
                        f" | section=Work Experience")
    for d in durations[:3]:
        evidence.append(f"{d['stated']} ({d['context']})")
    for e in exp_years[:3]:
        evidence.append(f"{e['stated']} ({e['context']})")

    return {
        "professional_years": prof_years,
        "professional_months": prof_months,
        "roles": entries,
        "merged_employment_months": date_months,
        "stated_duration_months": dur_months,
        "stated_years_claims": exp_years,
        "explicit_durations": durations,
        "project_years": round(project_months / 12.0, 2),
        "project_periods": project_entries,
        "education_years": round(edu_months / 12.0, 2),
        "education_periods": edu_entries,
        "confidence": confidence,
        "basis": "; ".join(basis),
        "note": note,
        "evidence": evidence[:8],
        "unverifiable": _unverifiable_starts(emp_block) if has_emp_section else [],
        "employment_section_found": has_emp_section,
        "sections_seen": sorted(sections.keys()),
    }


def professional_experience_years(raw_text, today=None):
    """Convenience wrapper: professional years only (float)."""
    return extract_experience_profile(raw_text, today)["professional_years"]