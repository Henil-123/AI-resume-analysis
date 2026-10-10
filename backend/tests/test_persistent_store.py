"""Persistent store: durability, audit trail, transactions, failure behaviour."""
import io
import os

os.environ["SEMANTIC_ENGINE"] = "tfidf"

import app as appmod
from database.store import (AnalysisNotFound, AnalysisStore, AnalysisStoreError,
                            VersionConflict)

RESUME = """Aarav Sharma
aarav.sharma@example.com

EDUCATION
B.Tech, Computer Science and Engineering
Dr. Vishwakarma Institute of Technology, 2021 - 2025

KEY SKILLS
Languages: Java, Python, JavaScript
Frameworks: React, Spring Boot
Tools: Git, MySQL

PROJECTS
1. CareerEdge Portal (2024 - 2025)
   Built a full-stack web application using React and JavaScript.
   Developed REST API endpoints in Spring Boot connected to MySQL.

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

Education Requirements
Minimum qualification: BE/BTech in Computer Science or related field.
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


def _payload():
    return {"resume_file": "v1_x.txt", "required_experience": 0,
            "job_description": JD[:5000], "jd_meta": {"job_title": "t"},
            "candidate": {"final_score": 38.1},
            "explainable": {
                "requirements": [], "requirement_scores": [],
                "aggregate": {"status": "ok", "overall_score": 38.1,
                              "formula_inputs": {"K": 39.1, "M": 15.1, "E": 100.0,
                                                 "overall_exact": 38.05},
                              "config_version": "2.0.0",
                              "formula_version": "explainable-v2",
                              "weights": {"keyword": 0.6, "semantic": 0.3,
                                          "experience": 0.1}},
                "eligibility": {"overall": "UNKNOWN"},
                "gaps": [], "report": {"executive_summary": {"overall_score": 38.1}},
                "config_version": "2.0.0", "formula_version": "explainable-v2"}}


# ── durability ────────────────────────────────────────────────
def test_analysis_survives_memory_wipe():
    """Simulated restart: _ANALYSES cleared, record still served identically."""
    c, aid = _analyze()
    before = c.get(f"/api/v1/analyses/{aid}").get_json()
    appmod._ANALYSES.clear()  # process memory wiped
    after = c.get(f"/api/v1/analyses/{aid}").get_json()
    assert after["candidate"]["final_score"] == before["candidate"]["final_score"]
    assert (after["explainable"]["aggregate"]["formula_inputs"] ==
            before["explainable"]["aggregate"]["formula_inputs"])


def test_record_readable_from_a_fresh_store_object():
    """A second instance (new store object, same URL) sees the same record."""
    c, aid = _analyze()
    before = c.get(f"/api/v1/analyses/{aid}").get_json()
    fresh = AnalysisStore(appmod._store().database_url)
    payload = fresh.get_analysis(aid)
    assert payload is not None
    assert (payload["explainable"]["aggregate"]["overall_score"] ==
            before["explainable"]["aggregate"]["overall_score"])


def test_ids_are_collision_resistant():
    c = _client()
    ids = set()
    for _ in range(3):
        _, aid = _analyze(c)
        ids.add(aid)
    assert len(ids) == 3
    assert all(len(i) == 32 and all(ch in "0123456789abcdef" for ch in i)
               for i in ids)


def test_missing_record_returns_not_found_not_leak():
    c = _client()
    r = c.get("/api/v1/analyses/" + "a" * 32)
    assert r.status_code == 404
    assert "Analysis not found" in r.get_json()["error"]
    assert "traceback" not in r.get_json()["error"].lower()


def test_invalid_id_rejected():
    c = _client()
    assert c.get("/api/v1/analyses/not-valid!!").status_code == 400
    assert c.get("/api/v1/analyses/../secret").status_code in (400, 404)


# ── audit trail ───────────────────────────────────────────────
def test_review_events_are_append_only():
    c, aid = _analyze()
    c.patch(f"/api/v1/analyses/{aid}/requirements", json={"exclude": ["typescript"]})
    c.patch(f"/api/v1/analyses/{aid}/requirements", json={"weights": {"java": 5}})
    events = c.get(f"/api/v1/analyses/{aid}/reviews").get_json()
    assert events["count"] == 2
    actions = [e["action"] for e in events["events"]]
    assert actions == ["exclude", "reweight"]
    # Original result is not erased: the first event still names typescript.
    assert events["events"][0]["requirement_id"] == "typescript"
    assert all(e["created_at"] for e in events["events"])


def test_invalid_review_action_rejected_by_store():
    store = appmod._store()
    aid = store.create_analysis(_payload())
    try:
        store.append_review_events(aid, [{"action": "delete-everything"}])
        assert False, "should have raised"
    except AnalysisStoreError as e:
        assert "Invalid review action" in str(e)


def test_version_conflict_on_stale_update():
    store = appmod._store()
    aid = store.create_analysis(_payload())
    payload = _payload()
    v2 = store.update_scores(aid, payload["explainable"])
    assert v2 == 2
    try:
        store.update_scores(aid, payload["explainable"], expected_version=1)
        assert False, "should have raised"
    except VersionConflict as e:
        assert e.current == 2 and e.expected == 1


def test_update_missing_record_raises_not_found():
    store = appmod._store()
    try:
        store.update_scores("b" * 32, {})
        assert False, "should have raised"
    except AnalysisNotFound:
        pass


# ── retention ─────────────────────────────────────────────────
def test_delete_removes_record_and_resume():
    c, aid = _analyze()
    stored = appmod._get_stored_analysis(aid)
    resume = stored.get("resume_file")
    assert resume and os.path.exists(
        os.path.join(appmod.UPLOAD_FOLDER, os.path.basename(resume)))
    r = c.delete(f"/api/v1/analyses/{aid}")
    assert r.status_code == 200
    assert c.get(f"/api/v1/analyses/{aid}").status_code == 404
    assert not os.path.exists(
        os.path.join(appmod.UPLOAD_FOLDER, os.path.basename(resume)))


def test_unavailable_database_fails_clearly():
    bad = AnalysisStore.__new__(AnalysisStore)
    import sqlalchemy as _sa
    bad.engine = _sa.create_engine("sqlite:////nonexistent-dir-xyz/db.sqlite")
    from sqlalchemy.orm import sessionmaker
    bad.sessions = sessionmaker(bind=bad.engine, expire_on_commit=False)
    bad.database_url = "sqlite:////nonexistent-dir-xyz/db.sqlite"
    try:
        bad.ping()
        assert False, "should have raised"
    except AnalysisStoreError as e:
        assert "unreachable" in str(e)
        assert "nonexistent-dir-xyz" not in str(e)  # no path/secret leak
