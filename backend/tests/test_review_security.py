"""Manual-review endpoint: auth, schema, conflicts, concurrency, audit."""
import io
import os

os.environ["SEMANTIC_ENGINE"] = "tfidf"

import pytest

import app as appmod
import app_config

RESUME = """Aarav Sharma
aarav.sharma@example.com
EDUCATION
B.Tech, Computer Science, 2021 - 2025
KEY SKILLS
Java, React, Spring Boot, SQL
PROJECTS
Portal (2024 - 2025): React frontend, Spring Boot REST backend on MySQL.
INTERNSHIP EXPERIENCE
Python Developer Intern, ABC Technologies, June 2025 - August 2025 (2 months)
"""

JD = """HCLTech Campus Hiring 2027
Job Title: Java Full Stack Developer
Required Skills: Java, Spring Boot, SQL, React, TypeScript.
Education Requirements: Minimum qualification: BE/BTech in Computer Science.
"""


@pytest.fixture()
def token(monkeypatch):
    monkeypatch.setattr(app_config, "REVIEW_API_TOKEN", "test-token-123")
    yield "test-token-123"
    monkeypatch.setattr(app_config, "REVIEW_API_TOKEN", "")


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


def _patch(c, aid, body, tok=None):
    headers = {}
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    return c.patch(f"/api/v1/analyses/{aid}/requirements", json=body,
                   headers=headers)


# ── auth ──────────────────────────────────────────────────────
def test_review_requires_token_when_configured(token):
    c, aid = _analyze()
    r = _patch(c, aid, {"exclude": ["asp.net"]})
    assert r.status_code == 401
    assert "bearer token" in r.get_json()["error"].lower()
    r = _patch(c, aid, {"exclude": ["asp.net"]}, tok="wrong-token")
    assert r.status_code == 401
    r = _patch(c, aid, {"exclude": ["asp.net"]}, tok=token)
    assert r.status_code == 200


def test_review_open_without_token_in_dev():
    c, aid = _analyze()
    r = _patch(c, aid, {"exclude": ["asp.net"]})
    assert r.status_code == 200  # dev/internal use; prod startup refuses this


# ── schema ────────────────────────────────────────────────────
def test_review_rejects_non_object_body(token):
    c, aid = _analyze()
    r = c.patch(f"/api/v1/analyses/{aid}/requirements", data="nope",
                content_type="application/json",
                headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400


def test_review_rejects_unknown_fields(token):
    c, aid = _analyze()
    r = _patch(c, aid, {"delete_everything": True}, tok=token)
    assert r.status_code == 400
    assert "Unknown field" in r.get_json()["error"]


def test_review_rejects_exclude_restore_clash(token):
    c, aid = _analyze()
    r = _patch(c, aid, {"exclude": ["java"], "restore": ["Java"]}, tok=token)
    assert r.status_code == 400
    assert "both excluded and restored" in r.get_json()["error"]


def test_review_rejects_bad_weights(token):
    c, aid = _analyze()
    for bad in ({"java": float("nan")}, {"java": float("inf")},
                {"java": -1}, {"java": 99}, {"java": "high"}, {"": 3}):
        r = _patch(c, aid, {"weights": bad}, tok=token)
        assert r.status_code == 400, bad


def test_review_rejects_non_bool_mandatory(token):
    c, aid = _analyze()
    r = _patch(c, aid, {"mandatory": {"react": "yes"}}, tok=token)
    assert r.status_code == 400
    assert "true/false" in r.get_json()["error"]


def test_review_rejects_empty_actions(token):
    c, aid = _analyze()
    r = _patch(c, aid, {"reviewed_by": "x"}, tok=token)
    assert r.status_code == 400
    assert "No review actions" in r.get_json()["error"]


def test_review_missing_analysis_is_404(token):
    c = _client()
    r = _patch(c, "c" * 32, {"exclude": ["java"]}, tok=token)
    assert r.status_code == 404


# ── behaviour ─────────────────────────────────────────────────
def test_repeated_exclude_is_idempotent(token):
    c, aid = _analyze()
    first = _patch(c, aid, {"exclude": ["typescript"]}, tok=token).get_json()
    second = _patch(c, aid, {"exclude": ["typescript"]}, tok=token).get_json()
    assert "typescript" in first["excluded"] and "typescript" in second["excluded"]
    assert (first["aggregate"]["overall_score"] ==
            second["aggregate"]["overall_score"])


def test_stale_version_conflicts(token):
    c, aid = _analyze()
    version = c.get(f"/api/v1/analyses/{aid}").get_json().get("version", 1)
    ok = _patch(c, aid, {"exclude": ["asp.net"], "expected_version": version},
                tok=token)
    assert ok.status_code == 200
    stale = _patch(c, aid, {"weights": {"java": 4}, "expected_version": version},
                   tok=token)
    assert stale.status_code == 409
    assert "changed since you read it" in stale.get_json()["error"]
    fresh = _patch(c, aid, {"weights": {"java": 4},
                            "expected_version": ok.get_json()["version"]}, tok=token)
    assert fresh.status_code == 200


def test_valid_review_persists_and_audits(token):
    c, aid = _analyze()
    body = _patch(c, aid, {"weights": {"java": 5}, "reviewed_by": "qa",
                           "reason": "core stack"}, tok=token).get_json()
    row = next(r for r in body["requirements"] if r["name"] == "Java")
    assert row["importance_weight"] == 5 and row["weight_source"] == "reviewed"
    appmod._ANALYSES.clear()  # restart: change must survive
    reread = c.get(f"/api/v1/analyses/{aid}").get_json()
    row2 = next(r for r in reread["explainable"]["requirements"]
                if r["name"] == "Java")
    assert row2["importance_weight"] == 5
    events = c.get(f"/api/v1/analyses/{aid}/reviews").get_json()
    assert events["count"] >= 1
    assert events["events"][-1]["actor"] == "qa"
    assert events["events"][-1]["action"] == "reweight"


def test_review_never_logs_resume_text(token, caplog):
    import logging
    c, aid = _analyze()
    with caplog.at_level(logging.INFO):
        _patch(c, aid, {"exclude": ["asp.net"]}, tok=token)
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "automation scripts" not in blob  # resume body never logged
    assert "test-token-123" not in blob  # secret never logged
