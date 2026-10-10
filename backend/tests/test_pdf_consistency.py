"""PDF/JSON consistency against the persisted authoritative record."""
import io
import os

os.environ["SEMANTIC_ENGINE"] = "tfidf"

import app as appmod

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

LONG_REQ_JD = """Job Title: Java Full Stack Developer
Required Skills: Java, Spring Boot, SQL, React, TypeScript.
Education Requirements: Minimum qualification: BE/BTech in Computer Science.
Additional context: """ + ("The candidate should demonstrate deep experience " * 40)


def _client():
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def _analyze(c, jd=None):
    r = c.post("/api/v1/analyses",
               data={"resume": (io.BytesIO(RESUME.encode()), "r.txt"),
                     "job_description": jd or LONG_REQ_JD,
                     "required_experience": "0"},
               content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()
    return r.get_json()["analysis_id"]


def _pdf_text(pdf_bytes):
    import re
    return re.sub(r"\\\\([()\\\\])", r"\1",
                  pdf_bytes.decode("latin-1", "replace"))


def test_pdf_matches_authoritative_score():
    c = _client()
    aid = _analyze(c)
    stored = c.get(f"/api/v1/analyses/{aid}").get_json()
    pdf = c.get(f"/api/v1/analyses/{aid}/report?format=pdf")
    assert pdf.status_code == 200
    text = _pdf_text(pdf.get_data())
    headline = stored["explainable"]["aggregate"]["overall_score"]
    assert f"Overall match: {headline}%" in text


def test_pdf_classifications_match_api():
    c = _client()
    aid = _analyze(c)
    api_rows = c.get(f"/api/v1/analyses/{aid}/requirements").get_json()["requirements"]
    text = _pdf_text(c.get(f"/api/v1/analyses/{aid}/report?format=pdf").get_data())
    for row in api_rows[:8]:
        assert row["name"] in text
    assert "Formula version" in text and "Config version" in text


def test_pdf_states_unknown_honestly():
    c = _client()
    aid = _analyze(c)
    rep = c.get(f"/api/v1/analyses/{aid}/report?format=json").get_json()
    text = _pdf_text(c.get(f"/api/v1/analyses/{aid}/report?format=pdf").get_data())
    overall = rep["eligibility_status"]["overall"]
    # Whatever the eligibility is, the PDF states it verbatim — and this
    # fixture has no verifiable bar violated, so it must not claim ELIGIBLE
    # unless the criteria back it.
    assert f"Overall: {overall}" in text
    assert overall in ("UNKNOWN", "VERIFIED_ELIGIBLE", "VERIFIED_INELIGIBLE")
    if overall == "UNKNOWN":
        assert "VERIFIED_ELIGIBLE" not in text


def test_pdf_reflects_manual_review():
    c = _client()
    aid = _analyze(c)
    c.patch(f"/api/v1/analyses/{aid}/requirements", json={"exclude": ["asp.net"]})
    stored = c.get(f"/api/v1/analyses/{aid}").get_json()
    text = _pdf_text(c.get(f"/api/v1/analyses/{aid}/report?format=pdf").get_data())
    assert f"Overall match: {stored['explainable']['aggregate']['overall_score']}%" in text


def test_long_descriptions_do_not_overlap_and_pages_break():
    c = _client()
    aid = _analyze(c)
    import re
    data = c.get(f"/api/v1/analyses/{aid}/report?format=pdf").get_data()
    ys = [int(m) for m in re.findall(rb"1 0 0 1 54 (\d+) Td", data)]
    assert len(ys) > 10
    assert data.count(b"/Type /Page") >= 3  # catalog+pages+page objects
    first = ys[0]
    run = []
    for y in ys:
        if y == first and run:
            break
        run.append(y)
    assert all(a > b for a, b in zip(run, run[1:]))


def test_pdf_generation_does_not_mutate_store():
    c = _client()
    aid = _analyze(c)
    before = c.get(f"/api/v1/analyses/{aid}").get_json()["explainable"]["aggregate"]
    for _ in range(3):
        assert c.get(f"/api/v1/analyses/{aid}/report?format=pdf").status_code == 200
    after = c.get(f"/api/v1/analyses/{aid}").get_json()["explainable"]["aggregate"]
    assert before == after


def test_report_correct_after_restart():
    c = _client()
    aid = _analyze(c)
    before_pdf = c.get(f"/api/v1/analyses/{aid}/report?format=pdf").get_data()
    before_json = c.get(f"/api/v1/analyses/{aid}/report?format=json").get_json()
    appmod._ANALYSES.clear()
    after_pdf = c.get(f"/api/v1/analyses/{aid}/report?format=pdf").get_data()
    after_json = c.get(f"/api/v1/analyses/{aid}/report?format=json").get_json()
    assert after_pdf == before_pdf
    assert (after_json["executive_summary"]["overall_score"] ==
            before_json["executive_summary"]["overall_score"])


def test_engine_warning_present_for_tfidf():
    c = _client()
    aid = _analyze(c)
    rep = c.get(f"/api/v1/analyses/{aid}/report?format=json").get_json()
    warning = rep["technical_audit"].get("semantic_engine_warning") or ""
    assert "TF-IDF" in warning
    assert "not transformer" in warning
