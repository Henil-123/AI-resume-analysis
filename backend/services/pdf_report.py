"""Minimal, dependency-free PDF writer for the screening report.

Purpose is parity, not typography: the PDF must render the SAME canonical
values as the headline, the breakdown, the JSON export and the API response.
It reads only the stored report object and never recomputes a score, so it
cannot disagree with the other surfaces.

Written by hand (PDF 1.4, base-14 Helvetica) because no PDF library is
available in this environment and adding one is unnecessary for a text-only
report.
"""
import textwrap


def _esc(s):
    """Escape a string for a PDF literal string."""
    if s is None:
        s = ""
    s = str(s)
    return (s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)"))


# WinAnsi-safe transliteration: the base-14 fonts use single-byte encoding and
# cannot render arbitrary Unicode (a common failure is an em dash silently
# producing an unreadable PDF).
_TRANSLIT = {
    "\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u00b7": "-",
    "\u00d7": "x", "\u2264": "<=", "\u2265": ">=", "\u2192": "->",
    "\u00b0": " deg", "\u00b2": "2", "\u00b5": "u", "\u20b9": "Rs ",
}


def _safe(s):
    s = "" if s is None else str(s)
    for k, v in _TRANSLIT.items():
        s = s.replace(k, v)
    return "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in s)


PAGE_W, PAGE_H = 612, 792
MARGIN_X, TOP_Y, BOTTOM_Y = 54, 760, 56


def _paginate(lines):
    """Split styled lines into pages, tracking a real y cursor.

    PDF `Td` sets an ABSOLUTE position inside the text object, so each line
    must be emitted with its own computed y. Reusing one origin made every
    line land on top of the previous one.
    """
    pages, ops, y = [], [], TOP_Y
    for style, text in lines:
        size, _font, indent, gap = style
        text = _safe(text)
        wrapped = (textwrap.wrap(text, width=max(int((PAGE_W - 2 * MARGIN_X
                                                      - indent) / (size * 0.5)), 24))
                   if text else [""])
        for i, ln in enumerate(wrapped or [""]):
            if y - size < BOTTOM_Y:
                pages.append(ops)
                ops, y = [], TOP_Y
            ops.append(f"BT /{_font} {size} Tf 1 0 0 1 {MARGIN_X + indent} "
                       f"{y:.0f} Td ({_esc(ln)}) Tj ET")
            y -= size + (gap if i == len(wrapped) - 1 else 2)
    pages.append(ops)
    return pages


def _build_document(pages):
    """Assemble a multi-page PDF from content-stream fragments."""
    n_pages = len(pages)
    # Object layout: 1 catalog, 2 pages, 3..3+N-1 page objects,
    # then N content streams, then the two fonts.
    font_f1 = 3 + 2 * n_pages
    font_f2 = font_f1 + 1

    objs = {}
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{3 + i} 0 R" for i in range(n_pages))
    objs[2] = (f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>"
               ).encode()
    for i, ops in enumerate(pages):
        page_no = 3 + i
        content_no = 3 + n_pages + i
        objs[page_no] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
            f"/Resources << /Font << /F1 {font_f1} 0 R /F2 {font_f2} 0 R >> >> "
            f"/Contents {content_no} 0 R >>").encode()
        stream = "\n".join(ops).encode("latin-1", "replace")
        objs[content_no] = (f"<< /Length {len(stream)} >>\nstream\n"
                             f"{stream.decode('latin-1')}\nendstream").encode(
            "latin-1")
    objs[font_f1] = (b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                     b"/Encoding /WinAnsiEncoding >>")
    objs[font_f2] = (b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
                     b"/Encoding /WinAnsiEncoding >>")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objs[num] + b"\nendobj\n"
    xref = len(out)
    size = max(objs) + 1
    out += b"xref\n0 %d\n" % size
    out += b"0000000000 65535 f \n"
    for num in range(1, size):
        out += b"%010d 00000 n \n" % offsets[num]
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF"
            % (size, xref))
    return bytes(out)


def render_report_pdf(report):
    """Render the canonical report dict to PDF bytes."""
    L = []  # (style, text); style = (size, font, indent, gap)

    def title(t):
        L.append(((18, "F2", 0, 10), t))

    def h1(t):
        L.append(((13, "F2", 0, 8), t))

    def h2(t):
        L.append(((10, "F2", 0, 5), t))

    def body(t, indent=0):
        L.append(((9, "F1", indent, 3), t))

    def gap():
        L.append(((9, "F1", 0, 4), ""))

    report = report or {}
    sb = report.get("score_breakdown") or {}
    fi = sb.get("formula_inputs") or {}
    es = report.get("executive_summary") or {}
    ve = report.get("verified_experience") or {}
    el = report.get("eligibility_status") or report.get("eligibility") or {}
    ta = report.get("technical_audit") or {}
    cand = report.get("candidate") or {}

    title("Resume Screening Report")
    if cand.get("name"):
        body("Candidate: %s" % cand["name"])
    body("Evaluated: %s" % (es.get("evaluated_at") or "n/a"))

    # ── 1. Primary overall score ──
    h1("1. Primary Overall Score")
    body("Overall match: %s%%" % es.get("overall_score"))
    body("Formula: %s" % (es.get("methodology") or sb.get("formula") or "n/a"))
    if fi:
        body("Exact inputs: K=%s, M=%s, E=%s; S=%s" % (
            fi.get("K"), fi.get("M"), fi.get("E"),
            fi.get("overall_exact")))
    gap()

    # ── 2-6. Component scores ──
    h1("2. Score Breakdown")
    body("Primary overall score: %s%%" % sb.get("primary_overall_score"))
    body("Keyword score (K): %s%%" % sb.get("keyword_coverage"))
    body("Semantic score (M): %s%%" % sb.get("semantic_relevance"))
    body("Experience-fit score (E): %s%%" % sb.get("experience_fit"))
    body("Requirement-weighted diagnostic score: %s%% (diagnostic only, "
         "not the headline)" % sb.get("requirement_weighted_diagnostic"))
    body("Unweighted skill coverage: %s%%" % sb.get("unweighted_skill_coverage"))
    body(sb.get("note") or "")
    gap()

    # ── 7. Eligibility ──
    h1("3. Eligibility Status")
    body("Overall: %s" % el.get("overall"))
    if el.get("note"):
        body(el["note"])
    for cr in el.get("criteria") or []:
        body("- %s: %s" % (cr.get("criterion"), cr.get("status")), indent=12)
        if cr.get("detail"):
            body(cr["detail"], indent=24)
        if cr.get("source"):
            body("Source: %s" % cr["source"], indent=24)
    if el.get("skill_gaps"):
        h2("Skill gaps (affect compatibility, not eligibility)")
        for g in el["skill_gaps"]:
            body("- %s: %s" % (g.get("skill"), g.get("detail")), indent=12)
    gap()

    # ── 8. Verified experience evidence ──
    h1("4. Verified Experience")
    body("Professional experience: %s years" % ve.get("professional_years"))
    body("Project experience: %s years (excluded from professional)"
         % ve.get("project_years"))
    body("Education: %s years (excluded from professional)"
         % ve.get("education_years"))
    body("Confidence: %s" % ve.get("confidence"))
    if ve.get("basis"):
        body("Basis: %s" % ve["basis"])
    if ve.get("note"):
        body(ve["note"])
    for e in ve.get("evidence") or []:
        body("- %s" % e, indent=12)
    for u in ve.get("unverifiable") or []:
        body("- UNVERIFIED: %s (%s)" % (u.get("found") or u.get("raw", "")[:60],
                                        u.get("reason")), indent=12)
    gap()

    # ── 9-11. Skills ──
    ms = report.get("matched_skills") or {}
    h1("5. Skills")
    h2("Directly matched")
    body(", ".join(ms.get("direct") or []) or "none", indent=12)
    h2("Transferable / partial")
    body(", ".join(ms.get("transferable_or_partial") or []) or "none", indent=12)
    h2("Mentioned in skills list only")
    body(", ".join(ms.get("skills_section_only") or []) or "none", indent=12)
    miss = report.get("missing_skills") or {}
    h2("Missing or unverified")
    for label in ("critical", "important", "secondary", "unverified"):
        vals = miss.get(label) or []
        if vals:
            body("%s: %s" % (label.capitalize(), ", ".join(vals)), indent=12)
    gap()

    # ── 12. Source-grounded recommendations ──
    h1("6. Recommendations")
    recs = report.get("recommendations") or []
    if not recs:
        body("No gaps identified.")
    for r in recs[:20]:
        body("- %s (%s): %s" % (r.get("requirement"),
                                 r.get("importance"),
                                 r.get("reason") or r.get("missing_or_unverified", "")),
             indent=12)
    gap()

    # ── 13. Requirement-level detail ──
    rows = report.get("requirements") or []
    if rows:
        h1("7. Requirement Detail")
        for r in rows[:60]:
            body("%s | w=%s (%s) | K=%s M=%s P=%s | R=%s | cosine=%s (%s)" % (
                r.get("name"), r.get("importance_weight"),
                r.get("weight_source") or "?", r.get("keyword_match"),
                r.get("semantic_score"), r.get("evidence_strength"),
                r.get("requirement_score"), r.get("raw_cosine"),
                r.get("semantic_band") or "-"), indent=6)
            if r.get("evidence_section"):
                body("Evidence: [%s p%s] %s" % (
                    r.get("evidence_section"), r.get("evidence_page"),
                    (r.get("resume_evidence") or "")[:120]), indent=18)

    # ── Model / config metadata ──
    h1("8. Model and Configuration")
    body("Embedding model: %s" % ta.get("semantic_model"))
    body("Formula: %s" % ta.get("formula"))
    body("Formula version: %s" % ta.get("formula_version"))
    body("Config version: %s" % ta.get("config_version"))
    body("Weights: %s" % ta.get("weights"))
    if ta.get("errors"):
        body("Errors: %s" % "; ".join(ta["errors"]))
    body(es.get("limitations") or "")

    # ── Render ──
    return _build_document(_paginate(L))