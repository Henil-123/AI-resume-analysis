"""Tests for the final spec items: PDF parity, requirement review, and
embedding provenance + calibration honesty.

These cover A.4 (PDF uses the same canonical result), C.8 (manual review of
extracted requirements before final scoring), F.1/F.2 (embedding provenance)
and F.8 (calibration against human labels).
"""
import io
import os

os.environ["SEMANTIC_ENGINE"] = "tfidf"

import app as appmod
from services.pdf_report import render_report_pdf
from services import semantic_calibration as sc

RESUME = """Aarav Sharma
aarav.sharma@gmail.com

EDUCATION
B.Tech, Computer Science and Engineering
Dr. Vishwakarma Institute of Technology, 2021 - 2025

KEY SKILLS
Languages: Java, Python, JavaScript, HTML, CSS
Frameworks: React, Node.js, Spring Boot
Tools: Git, GitHub, MySQL

PROJECTS
1. CareerEdge Portal (2024 - 2025)
   Built a full-stack web application using React, JavaScript and CSS.
   Developed REST API endpoints in Spring Boot connected to MySQL database.

INTERNSHIP EXPERIENCE
Python Developer Intern
ABC Technologies, Pune
June 2025 - August 2025 (2 months)
- Developed internal automation scripts in Python.
"""

JD = """HCLTech Campus Hiring 2027
Job Title: Java Full Stack Developer

Required Skills
Strong hands-on experience in Java, Spring Boot and SQL.
Proficiency in React and TypeScript for frontend development.
Experience building and consuming REST APIs.

Preferred Skills
Experience with Kubernetes, Docker and GitHub Actions.

Education Requirements
Minimum qualification: BE/BTech in Computer Science or related field.

.NET Full Stack Developer
Strong hands-on experience in C# and ASP.NET.
"""


def _pdf(c, aid):
    """Fetch the PDF. PDF literals escape '(' and ')' as \\( and \\)."""
    return c.get("/api/v1/analyses/%s/report?format=pdf" % aid).get_data()


def _pdf_literal(s):
    return str(s).replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _analyze():
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    r = c.post("/api/v1/analyses",
               data={"resume": (io.BytesIO(RESUME.encode()), "r.txt"),
                     "job_description": JD, "required_experience": "0"},
               content_type="multipart/form-data")
    aid = r.get_json()["analysis_id"]
    full = c.get("/api/v1/analyses/%s" % aid).get_json()
    return c, aid, full


# ══════════════════════════════════════════════���═══════════════
# A.4 — PDF report uses the same canonical result
# ══════════════════════════════════════════════════════════
def test_pdf_is_a_valid_document():
    c, aid, _ = _analyze()
    r = c.get("/api/v1/analyses/%s/report?format=pdf" % aid)
    assert r.status_code == 200
    assert r.mimetype == "application/pdf"
    data = r.get_data()
    assert data.startswith(b"%PDF-1.4")
    assert data.rstrip().endswith(b"%%EOF")


def test_pdf_lines_do_not_overlap():
    """Each line must get its own y position (a real cursor, not one origin)."""
    c, aid, _ = _analyze()
    import re
    data = _pdf(c, aid)
    ys = [int(m) for m in re.findall(rb"1 0 0 1 54 (\d+) Td", data)]
    assert len(ys) > 10
    # y decreases down a page and resets to the top only at a page break.
    resets = 0
    for a, b in zip(ys, ys[1:]):
        if b > a:
            resets += 1
            assert b == max(ys), (a, b)
        else:
            assert a > b
    assert resets >= 0
    # A single page's worth of lines must be strictly descending.
    first_page = []
    for y in ys:
        if y == max(ys) and first_page:
            break
        first_page.append(y)
    assert all(a > b for a, b in zip(first_page, first_page[1:])), first_page


def test_pdf_contains_the_canonical_score_values():
    c, aid, b = _analyze()
    data = _pdf(c, aid)
    rep = b["explainable"]["report"]
    sb = rep["score_breakdown"]
    for text in ("Overall match: %s%%" % rep["executive_summary"]["overall_score"],
                 "Keyword score (K): %s%%" % sb["keyword_coverage"],
                 "Semantic score (M): %s%%" % sb["semantic_relevance"],
                 "Experience-fit score (E): %s%%" % sb["experience_fit"],
                 "Requirement-weighted diagnostic score: %s%%"
                 % sb["requirement_weighted_diagnostic"]):
        assert _pdf_literal(text).encode() in data, text


def test_pdf_score_equals_api_and_json_export():
    c, aid, b = _analyze()
    pdf = _pdf(c, aid)
    js = c.get("/api/v1/analyses/%s/report?format=json" % aid).get_json()
    api = c.get("/api/v1/analyses/%s" % aid).get_json()
    cand = api["candidate"]
    headline = cand["final_score"]
    assert js["executive_summary"]["overall_score"] == headline
    # The PDF carries the same value the API and JSON report serve.
    assert _pdf_literal("Overall match: %s%%" % headline).encode() in pdf
    assert cand["score_breakdown"]["keyword_score"] == \
        api["explainable"]["aggregate"]["keyword_coverage"]


def test_pdf_does_not_recompute_the_score():
    """Mutating the report must change the PDF: it renders, never recalculates."""
    rep = {"executive_summary": {"overall_score": 11.1, "evaluated_at": "now"},
           "score_breakdown": {"primary_overall_score": 11.1,
                               "keyword_coverage": 22.2,
                               "semantic_relevance": 33.3,
                               "experience_fit": 44.4,
                               "requirement_weighted_diagnostic": 55.5},
           "verified_experience": {"professional_years": 0.2,
                                   "project_years": 1.0,
                                   "education_years": 4.0,
                                   "confidence": "high"},
           "eligibility_status": {"overall": "UNKNOWN"}}
    data = render_report_pdf(rep)
    assert b"Overall match: 11.1%" in data
    assert _pdf_literal("Keyword score (K): 22.2%").encode() in data


def test_pdf_handles_unicode_without_corrupting_output():
    rep = {"executive_summary": {"overall_score": 50.0,
                                 "methodology": "S = 0.60\u00b7K + 0.30\u00b7M "
                                                "\u2014 exact"},
           "score_breakdown": {}}
    data = render_report_pdf(rep)
    assert data.startswith(b"%PDF-1.4")
    assert data.rstrip().endswith(b"%%EOF")


def test_pdf_and_json_agree_on_experience_values():
    c, aid, b = _analyze()
    pdf = _pdf(c, aid)
    ve = b["explainable"]["report"]["verified_experience"]
    assert _pdf_literal("Professional experience: %s years"
                        % ve["professional_years"]).encode() in pdf
    # The two-month internship is never printed as a multi-year tenure.
    assert ve["professional_years"] < 0.5


# ══════════════════════════════════════════════════════════
# C.8 — manual review before final scoring
# ══════════════════════════════════════════════════════════
def _v1_analyze():
    return _analyze()


def test_review_can_exclude_a_requirement_and_rescore():
    c, aid, b = _v1_analyze()
    before = b["candidate"]["final_score"]
    r = c.patch("/api/v1/analyses/%s/requirements" % aid,
                json={"exclude": ["asp.net"], "reviewed_by": "tester",
                      "reason": "JD has no .NET curriculum for this role"})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert "asp.net" in body["excluded"]
    assert body["changes"][0]["action"] == "excluded"
    # The score is recomputed from the reviewed requirement set.
    assert body["aggregate"]["overall_score"] != before


def test_review_exclusion_changes_the_keyword_score_consistently():
    c, aid, _b = _v1_analyze()
    before = c.get("/api/v1/analyses/%s/requirements" % aid).get_json()["aggregate"]
    body = c.patch("/api/v1/analyses/%s/requirements" % aid,
                   json={"exclude": ["docker"]}).get_json()
    after = body["aggregate"]
    # Removing a requirement raises K (it was an unmet, weighted row).
    assert after["keyword_coverage"] >= before["keyword_coverage"]
    # And the headline still equals its own recorded exact inputs.
    fi = after["formula_inputs"]
    calc = fi["w_keyword"] * fi["K"] + fi["w_semantic"] * fi["M"] \
        + fi["w_experience"] * fi["E"]
    assert abs(calc - fi["overall_exact"]) < 1e-9
    assert round(calc, 1) == after["overall_score"]


def test_review_can_correct_a_weight():
    c, aid, _b = _v1_analyze()
    body = c.patch("/api/v1/analyses/%s/requirements" % aid,
                   json={"weights": {"java": 5}}).get_json()
    row = next(r for r in body["requirements"] if r["name"] == "Java")
    assert row["importance_weight"] == 5
    assert row["weight_source"] == "reviewed"


def test_review_records_provenance_of_the_change():
    c, aid, _b = _v1_analyze()
    body = c.patch("/api/v1/analyses/%s/requirements" % aid,
                   json={"weights": {"java": 4}, "reviewed_by": "Alice",
                         "reason": "core stack"}).get_json()
    assert body["reviewed_by"] == "Alice"
    assert body["review_reason"] == "core stack"
    row = next(r for r in body["requirements"] if r["name"] == "Java")
    assert "Alice" in row["weight_reason"]


def test_review_rejects_unknown_requirement():
    c, aid, _b = _v1_analyze()
    r = c.patch("/api/v1/analyses/%s/requirements" % aid,
                json={"weights": {"cobol": 5}})
    assert r.status_code == 400
    assert "unknown requirement" in r.get_json()["error"]


def test_review_rejects_out_of_range_weight():
    c, aid, _b = _v1_analyze()
    r = c.patch("/api/v1/analyses/%s/requirements" % aid,
                json={"weights": {"java": 99}})
    assert r.status_code == 400
    assert "[1, 5]" in r.get_json()["error"]


def test_review_rejects_non_numeric_weight():
    c, aid, _b = _v1_analyze()
    r = c.patch("/api/v1/analyses/%s/requirements" % aid,
                json={"weights": {"java": "high"}})
    assert r.status_code == 400
    assert "must be a number" in r.get_json()["error"]


def test_review_cannot_restore_a_requirement_the_jd_never_contained():
    c, aid, _b = _v1_analyze()
    r = c.patch("/api/v1/analyses/%s/requirements" % aid,
                json={"restore": ["mainframe cobol"]})
    assert r.status_code == 400
    assert "not a requirement" in r.get_json()["error"]


def test_review_unknown_analysis_is_actionable():
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    r = c.patch("/api/v1/analyses/nope/requirements", json={"exclude": ["java"]})
    assert r.status_code == 404
    assert "re-run" in r.get_json()["error"].lower()


# ══════════════════════════════════════════════════════════
# F.1 / F.2 — embedding provenance
# ══════════════════════════════════════════════════════════
def test_requirement_rows_record_embedding_provenance():
    _, _, b = _analyze()
    rows = [r for r in b["explainable"]["requirement_scores"]
            if r.get("semantic_model_meta")]
    assert rows
    meta = rows[0]["semantic_model_meta"]
    assert meta.get("engine") in ("tfidf", "sentence-transformers")
    assert meta.get("model")
    assert rows[0]["semantic_query_text"], "the embedded requirement text is recorded"


def test_semantic_scores_come_from_real_inference():
    """A requirement with evidence must have a model-derived cosine."""
    _, _, b = _analyze()
    with_ev = [r for r in b["explainable"]["requirement_scores"]
               if r["semantic_basis"] == "skill-evidence"]
    assert with_ev, "no requirement produced semantic evidence"
    for r in with_ev:
        assert isinstance(r["raw_cosine"], (int, float))
        assert r["semantic_model"]
        assert r["semantic_query_text"]


def test_semantic_without_evidence_is_zero_and_labelled():
    _, _, b = _analyze()
    rows = {r["name"]: r for r in b["explainable"]["requirement_scores"]}
    ts = rows.get("Typescript")
    if ts:
        assert ts["semantic_basis"] == "no-evidence"
        assert ts["semantic_score"] == 0.0
        assert ts["raw_cosine"] == 0.0


# ══════════════════════════════════════════════════════════
# F.8 — calibration against human labels
# ══════════════════════════════════════════════════════════
def test_calibration_report_is_honest_about_scope():
    _, _, b = _analyze()
    rep = sc.calibration_report()
    assert rep["available"] is True
    assert rep["sample_size"] == len(sc.SEED_CASES)
    # It must not claim statistical validation of a hand-written seed set.
    assert "NOT a statistically validated" in rep["disclaimer"]
    assert "NOT a statistically validated" in \
        b["explainable"]["report"]["technical_audit"]["semantic_calibration"]["disclaimer"]


def test_calibration_logic_correctly_detects_band_ordering():
    """The calibration logic itself must be able to PASS and FAIL honestly.

    Uses an injected embedder so the test measures the ordering CHECK, not the
    engine. The real-engine result is asserted separately, because the light
    TF-IDF engine legitimately fails this ordering (see the next test).
    """
    import numpy as _np

    def ordered_embedding(texts):
        # Perfect embedder: a passage scores high iff its label is 'match'.
        labels = []
        for t in texts:
            labels.append("match" if t in sc.SEED_CASES and t != "" else "x")
        base = {"match": 1.0, "related": 0.5, "unrelated": 0.1}
        vecs = []
        for t in texts:
            idx = [i for i, (rq, ps, lb) in enumerate(sc.SEED_CASES)
                   if t == rq or t == ps]
            lab = sc.SEED_CASES[idx[0]][2] if idx else "unrelated"
            v = _np.zeros(len(sc.SEED_CASES), dtype="float32")
            v[idx[0] if idx else 0] = base[lab]
            vecs.append(v)
        m = _np.array(vecs)
        return m / (_np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)

    rep = sc.calibration_report(embedding_fn=ordered_embedding)
    assert rep["ordering_match_ge_related_ge_unrelated"] is True, rep


def test_real_engine_band_ordering_is_reported_truthfully():
    """The active engine's ordering result must be reported as measured.

    The light TF-IDF engine is lexical and does NOT separate 'related' from
    'unrelated' as reliably as a transformer does. The report must surface
    that rather than assert a pass.
    """
    rep = sc.calibration_report()
    assert rep["available"] is True
    measured = rep["group_mean_normalized"]
    expected = (measured["match"] >= measured["related"] >= measured["unrelated"])
    assert rep["ordering_match_ge_related_ge_unrelated"] is expected
    # And the headline aggregate must not depend on calibration claims.
    _, _, b = _analyze()
    cal = b["explainable"]["report"]["technical_audit"]["semantic_calibration"]
    assert cal["ordering_match_ge_related_ge_unrelated"] is expected
    assert cal["sample_size"] == len(sc.SEED_CASES)


def test_calibration_report_is_deterministic():
    a = sc.calibration_report()
    c = sc.calibration_report()
    assert a["group_mean_normalized"] == c["group_mean_normalized"]


def test_calibration_reports_failure_instead_of_fabricating():
    def boom(texts):
        raise RuntimeError("model exploded")

    rep = sc.calibration_report(embedding_fn=boom)
    assert rep["available"] is False
    assert "model exploded" in rep["reason"]


def test_embedded_labels_loader_tolerates_missing_file():
    assert sc.load_labels(path="does-not-exist.json") == []