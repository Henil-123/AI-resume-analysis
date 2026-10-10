"""API/frontend consistency: every consumer reads the same stored values."""
import io
import os

os.environ["SEMANTIC_ENGINE"] = "tfidf"
import app as appmod

JD = """Java Full Stack Developer
Required Skills: Java, Spring Boot, React, SQL, REST API
Preferred: Docker, Kubernetes
Minimum 1 year experience."""

RESUME = """Ravi Patel
Education
B.Tech Computer Science, 2023
Skills: Java, React, SQL
Projects: CareerEdge web app using Java and React.
Work Experience
Python Developer Intern, ABC Corp (2 months)
"""


def test_every_surfaced_value_agrees():
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    r = c.post("/analyze", data={"resume": (io.BytesIO(RESUME.encode()), "r.txt"),
                                 "job_description": JD, "required_experience": "1"},
               content_type="multipart/form-data")
    b = r.get_json()
    agg = b["explainable"]["aggregate"]
    s = agg["overall_score"]

    # the four places the UI/export read a headline
    assert b["final_score"] == s
    assert b["explainable"]["overall_score"] == s
    assert b["report"]["executive_summary"]["overall_score"] == s
    fi = b["score_breakdown"]["formula_inputs"]
    # Exact inputs re-derive the headline; display fields are rounded.
    assert round(fi["w_keyword"] * fi["K"] + fi["w_semantic"] * fi["M"]
                 + fi["w_experience"] * fi["E"], 1) == s

    # breakdown values the UI renders
    bd = b["score_breakdown"]
    assert (bd["keyword_score"], bd["semantic_score"], bd["experience_score"]) == \
           (agg["keyword_coverage"], agg["semantic_relevance"], agg["experience_fit"])

    # report breakdown mirrors the same numbers
    rb = b["report"]["score_breakdown"]
    assert rb["keyword_coverage"] == agg["keyword_coverage"]
    assert rb["semantic_relevance"] == agg["semantic_relevance"]
    assert rb["experience_fit"] == agg["experience_fit"]

    # v1 API agrees with the legacy route
    v1 = c.post("/api/v1/analyses",
                data={"resume": (io.BytesIO(RESUME.encode()), "r.txt"),
                      "job_description": JD, "required_experience": "1"},
                content_type="multipart/form-data")
    assert v1.status_code == 201
    v1b = v1.get_json()
    assert v1b["report"]["executive_summary"]["overall_score"] == \
           v1b["candidate"]["explainable"]["aggregate"]["overall_score"]
    reqs = c.get(f"/api/v1/analyses/{v1b['analysis_id']}/requirements").get_json()
    assert reqs["aggregate"] == v1b["candidate"]["explainable"]["aggregate"]


def test_repeated_analysis_is_deterministic():
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    out = []
    for _ in range(2):
        r = c.post("/analyze", data={"resume": (io.BytesIO(RESUME.encode()), "r.txt"),
                                     "job_description": JD, "required_experience": "1"},
                   content_type="multipart/form-data")
        out.append(r.get_json()["explainable"]["aggregate"]["overall_score"])
    assert out[0] == out[1], f"non-deterministic scores: {out}"


def test_candidate_persists_authoritative_fields():
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    r = c.post("/analyze", data={"resume": (io.BytesIO(RESUME.encode()), "r.txt"),
                                 "job_description": JD, "required_experience": "1"},
               content_type="multipart/form-data")
    b = r.get_json()
    stored = c.get(f"/candidates/{b['id']}").get_json()
    if isinstance(stored, dict) and "candidate" in stored:
        stored = stored["candidate"]
    assert stored["final_score"] == b["final_score"]
    assert stored["score_breakdown"]["keyword_score"] == \
           b["explainable"]["aggregate"]["keyword_coverage"]