"""Scoring has one source of truth: validation, precision, agreement."""
import io
import math
import os

os.environ["SEMANTIC_ENGINE"] = "tfidf"

import app as appmod
from services import scoring_engine as se

RESUME = """Aarav Sharma
aarav.sharma@example.com

EDUCATION
B.Tech, Computer Science and Engineering, 2021 - 2025

KEY SKILLS
Java, Python, JavaScript, React, Spring Boot, MySQL, Git

PROJECTS
CareerEdge Portal (2024 - 2025)
Built a full-stack web application using React and JavaScript with a Spring Boot REST backend on MySQL.

INTERNSHIP EXPERIENCE
Python Developer Intern, ABC Technologies, Pune
June 2025 - August 2025 (2 months)
"""

JD = """HCLTech Campus Hiring 2027
Job Title: Java Full Stack Developer
Required Skills: Java, Spring Boot, SQL, React, TypeScript, REST APIs.
Education Requirements: Minimum qualification: BE/BTech in Computer Science.
"""


def _client():
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def _analyze(client=None):
    c = client or _client()
    r = c.post("/api/v1/analyses",
               data={"resume": (io.BytesIO(RESUME.encode()), "r.txt"),
                     "job_description": JD, "required_experience": "0"},
               content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()
    return c, r.get_json()["analysis_id"]


def _req_row(**kw):
    row = {"name": "Java", "canonical_skill": "java", "keyword_match": 1.0,
           "semantic_score": 0.5, "evidence_strength": 0.7,
           "importance_weight": 3, "requirement_score": 70.0}
    row.update(kw)
    return row


# ── validation ────────────────────────────────────────────────
def test_aggregate_rejects_nan():
    rows = [_req_row(semantic_score=float("nan"))]
    try:
        se.aggregate(rows, 100.0)
        assert False, "should have raised"
    except ValueError as e:
        assert "not finite" in str(e)


def test_aggregate_rejects_infinity():
    rows = [_req_row(keyword_match=float("inf"))]
    try:
        se.aggregate(rows, 100.0)
        assert False, "should have raised"
    except ValueError as e:
        assert "not finite" in str(e) or "outside" in str(e)


def test_aggregate_rejects_missing_component():
    rows = [_req_row(semantic_score=None)]
    try:
        se.aggregate(rows, 100.0)
        assert False, "should have raised"
    except ValueError as e:
        assert "missing" in str(e)


def test_aggregate_rejects_out_of_range():
    rows = [_req_row(semantic_score=150.0)]
    try:
        se.aggregate(rows, 100.0)
        assert False, "should have raised"
    except ValueError as e:
        assert "outside [0, 100]" in str(e)


def test_aggregate_rejects_bad_experience():
    rows = [_req_row()]
    for bad in (float("nan"), "lots", None):
        try:
            se.aggregate(rows, bad)
            assert False, f"should have raised for {bad!r}"
        except ValueError:
            pass


def test_aggregate_rejects_non_numeric():
    rows = [_req_row(keyword_match="high")]
    try:
        se.aggregate(rows, 100.0)
        assert False, "should have raised"
    except ValueError as e:
        assert "not a number" in str(e)


# ── precision + agreement ─────────────────────────────────────
def test_headline_equals_independent_recompute():
    c, aid = _analyze()
    agg = c.get(f"/api/v1/analyses/{aid}").get_json()["explainable"]["aggregate"]
    fi = agg["formula_inputs"]
    calc = fi["w_keyword"] * fi["K"] + fi["w_semantic"] * fi["M"] + fi["w_experience"] * fi["E"]
    assert abs(calc - fi["overall_exact"]) < 1e-9
    assert round(calc, 1) == agg["overall_score"]
    assert agg["overall_score"] == c.get(
        f"/api/v1/analyses/{aid}").get_json()["candidate"]["final_score"]


def test_decimal_precision_not_rounded_midway():
    rows = [_req_row(keyword_match=0.333333, semantic_score=0.666667,
                     evidence_strength=0.5, importance_weight=3,
                     requirement_score=50.0)]
    agg = se.aggregate(rows, 33.333333)
    fi = agg["formula_inputs"]
    assert fi["K"] != round(fi["K"], 1) or fi["M"] != round(fi["M"], 1)
    calc = fi["w_keyword"] * fi["K"] + fi["w_semantic"] * fi["M"] + fi["w_experience"] * fi["E"]
    assert abs(calc - fi["overall_exact"]) < 1e-12


def test_repeated_rescoring_is_stable():
    c, aid = _analyze()
    first = c.get(f"/api/v1/analyses/{aid}").get_json()["explainable"]["aggregate"]
    for weights in ({"keyword": 0.5, "semantic": 0.3, "experience": 0.2},
                    {"keyword": 0.6, "semantic": 0.3, "experience": 0.1}):
        r = c.post(f"/api/v1/analyses/{aid}/recalculate", json={"weights": weights})
        assert r.status_code == 200, r.get_json()
        agg = r.get_json()["aggregate"]
        fi = agg["formula_inputs"]
        calc = fi["w_keyword"] * fi["K"] + fi["w_semantic"] * fi["M"] + fi["w_experience"] * fi["E"]
        assert abs(calc - fi["overall_exact"]) < 1e-9
        assert round(calc, 1) == agg["overall_score"]
    # Stored record matches the last recalculation.
    stored = c.get(f"/api/v1/analyses/{aid}").get_json()["explainable"]["aggregate"]
    assert stored["overall_score"] == agg["overall_score"]
    assert stored["weights"] == {"keyword": 0.6, "semantic": 0.3, "experience": 0.1}


def test_invalid_weights_rejected_with_values_named():
    c, aid = _analyze()
    r = c.post(f"/api/v1/analyses/{aid}/recalculate",
               json={"weights": {"keyword": 0.5, "semantic": 0.5, "experience": 0.5}})
    assert r.status_code == 400
    assert "1.500" in r.get_json()["error"]
    r = c.post(f"/api/v1/analyses/{aid}/recalculate",
               json={"weights": {"keyword": "lots", "semantic": 0.3, "experience": 0.1}})
    assert r.status_code == 400
    r = c.post(f"/api/v1/analyses/{aid}/recalculate", json={})
    assert r.status_code == 400


def test_frontend_never_recomputes_headline():
    """The served candidate score and the report headline are one value."""
    c, aid = _analyze()
    body = c.get(f"/api/v1/analyses/{aid}").get_json()
    rep = c.get(f"/api/v1/analyses/{aid}/report?format=json").get_json()
    assert body["candidate"]["final_score"] == rep["executive_summary"]["overall_score"]
    sb = rep["score_breakdown"]
    assert sb["primary_overall_score"] == body["candidate"]["final_score"]
