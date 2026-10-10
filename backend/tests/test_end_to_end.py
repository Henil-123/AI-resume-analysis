"""End-to-end: upload docs -> analysis -> report; UI numbers match backend."""
import io
import os

os.environ["SEMANTIC_ENGINE"] = "tfidf"
import app as appmod

JD = """Senior Python Developer
Must have: Python, PostgreSQL, Docker
Preferred: Kubernetes
Minimum 3 years experience. Bachelors degree required.
Responsibilities: build and deploy Python services with PostgreSQL."""

RESUME = """Aarav Sharma
aarav@example.com
Skills: Python, PostgreSQL, Docker
Experience: Senior Python Developer 2020-Present. Built and deployed a
Dockerized Python API backed by PostgreSQL with integration tests.
Education: Bachelors in Computer Science."""


def _client():
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def test_v1_full_flow_numbers_agree():
    c = _client()
    data = {"resume": (io.BytesIO(RESUME.encode()), "resume.txt"),
            "job_description": JD,
            "required_experience": "3"}
    r = c.post("/api/v1/analyses", data=data, content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    agg = body["candidate"]["explainable"]["aggregate"]
    # independent recomputation from the returned requirement rows
    rows = body["candidate"]["explainable"]["requirement_scores"]
    tot = sum(x["importance_weight"] for x in rows)
    K = 100 * sum(x["importance_weight"] * x["keyword_match"] for x in rows) / tot
    S = round(0.6 * K + 0.3 * agg["semantic_relevance"] + 0.1 * agg["experience_fit"], 1)
    # ±0.2 tolerance: backend rounds components before the final blend (documented)
    assert abs(agg["overall_score"] - S) <= 0.2
    assert agg["keyword_coverage"] == round(K, 1)
    # report mirrors backend values
    rep = body["report"]
    assert rep["score_breakdown"]["keyword_coverage"] == agg["keyword_coverage"]
    assert rep["executive_summary"]["overall_score"] == agg["overall_score"]

    aid = body["analysis_id"]
    r2 = c.get(f"/api/v1/analyses/{aid}/requirements")
    assert r2.status_code == 200
    assert len(r2.get_json()["requirements"]) == len(rows)
    r3 = c.get(f"/api/v1/analyses/{aid}/report")
    assert r3.status_code == 200
    assert r3.get_json()["technical_audit"]["formula_version"] == "explainable-v2"


def test_v1_rejects_empty_resume():
    c = _client()
    data = {"resume": (io.BytesIO(b""), "empty.txt"), "job_description": JD}
    r = c.post("/api/v1/analyses", data=data, content_type="multipart/form-data")
    assert r.status_code in (400, 422, 500)


def test_legacy_analyze_still_works_with_explainable_attached():
    c = _client()
    data = {"resume": (io.BytesIO(RESUME.encode()), "resume.txt"),
            "job_description": JD, "required_experience": "3"}
    r = c.post("/analyze", data=data, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert "final_score" in body and "explainable" in body
