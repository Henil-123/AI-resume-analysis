"""Regression tests for the audited scoring-pipeline inconsistencies.

Each test maps to a concrete defect found in the audit:
  R1 two scoring authorities (headline != breakdown)
  R2 semantic M computed against arbitrary unrelated passages
  R3 two-month internship / sub-year experience invisible
  R4 skills & project evidence mislabelled as Education
  R5 every skill marked mandatory when JD headers were unrecognised
  R6 formula inputs + config version not recorded with the result
"""
import io
import os

os.environ["SEMANTIC_ENGINE"] = "tfidf"

import app as appmod
from services import scoring_engine as se
from services.section_parser import parse_resume_sections
from services.skill_extractor import extract_experience_years
from services.requirement_extractor import extract_requirements

JD_JAVA = """Java Full Stack Developer
Required Skills: Java, Spring Boot, React, SQL, REST API
Preferred: Docker, Kubernetes
Minimum 1 year experience. Bachelors degree required."""

RESUME = """Ravi Patel
Education
B.Tech Computer Science, 2023
Skills: Java, React, SQL
Projects: CareerEdge web app using Java and React.
Work Experience
Python Developer Intern, ABC Corp (2 months)
"""


def _post(resume=RESUME, jd=JD_JAVA, exp="1"):
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    r = c.post("/analyze", data={"resume": (io.BytesIO(resume.encode()), "r.txt"),
                                 "job_description": jd, "required_experience": exp},
               content_type="multipart/form-data")
    return r, r.get_json()


# ── R1: one authoritative score ────────────────────────────────────────
def test_headline_equals_formula_result():
    _, b = _post()
    agg = b["explainable"]["aggregate"]
    K, M, E = agg["keyword_coverage"], agg["semantic_relevance"], agg["experience_fit"]
    assert b["final_score"] == agg["overall_score"]
    assert b["explainable"]["overall_score"] == agg["overall_score"]
    assert b["report"]["executive_summary"]["overall_score"] == agg["overall_score"]
    # documented formula, from published inputs, within rounding
    expected = 0.60 * K + 0.30 * M + 0.10 * E
    assert abs(b["final_score"] - expected) <= 0.1
    assert b["score_breakdown"]["keyword_score"] == K
    assert b["score_breakdown"]["semantic_score"] == M
    assert b["score_breakdown"]["experience_score"] == E


def test_legacy_score_retained_but_not_headlined():
    _, b = _post()
    assert "legacy_final_score" in b
    assert b["final_score"] == b["explainable"]["aggregate"]["overall_score"]


def test_formula_inputs_are_recorded():
    _, b = _post()
    fi = b["explainable"]["aggregate"]["formula_inputs"]
    for k in ("K", "M", "E", "w_keyword", "w_semantic", "w_experience",
              "requirement_count", "total_weight"):
        assert k in fi
    assert fi["K"] == b["explainable"]["aggregate"]["keyword_coverage"]
    assert fi["M"] == b["explainable"]["aggregate"]["semantic_relevance"]
    assert fi["E"] == b["explainable"]["aggregate"]["experience_fit"]


def test_config_version_recorded_everywhere():
    _, b = _post()
    v = b["explainable"]["aggregate"]["config_version"]
    assert v
    assert b["explainable"]["config_version"] == v
    assert b["report"]["technical_audit"]["config_version"] == v


# ── R2: semantic only uses the skill's own evidence ────────────────────
def test_semantic_zero_when_no_evidence_for_skill():
    req = {"requirement_id": "req_001", "name": "Kubernetes",
           "canonical_skill": "kubernetes", "description": "Orchestrate with Kubernetes"}
    out = se.score_semantic(req, [], "Java React SQL project resume text")
    assert out["normalized"] == 0.0
    assert out["raw_cosine"] == 0.0
    assert out["basis"] == "no-evidence"


def test_aggregate_m_not_inflated_by_unrelated_evidence():
    _, b = _post()
    rows = b["explainable"]["requirement_scores"]
    for r in rows:
        if r["name"] == "Spring Boot":
            assert r["semantic_basis"] == "no-evidence"
            assert r["semantic_score"] == 0.0


def test_raw_cosine_distinct_from_normalized():
    _, b = _post()
    for r in b["explainable"]["requirement_scores"]:
        assert "raw_cosine" in r and "semantic_score" in r
        if r["semantic_basis"] == "skill-evidence":
            assert r["semantic_score"] >= 0.0 and r["semantic_score"] <= 1.0


# ── R3: experience durations ───────────────────────────────────────────
def test_two_month_internship_is_detected():
    years = extract_experience_years("Python Developer Intern, ABC Corp (2 months)")
    assert 0.15 <= years <= 0.2, years


def test_date_range_survives_text_cleaning():
    # clean_text strips punctuation ("2022-Present" -> "2022 present"), which
    # previously zeroed every date-derived experience signal.
    from services.resume_parser import clean_text
    cleaned = clean_text("Senior Java Developer, ABC Corp 2022-Present.")
    assert extract_experience_years(cleaned) >= 3.0


def test_professional_experience_detected_end_to_end():
    _, b = _post(resume="Ravi Patel\nSkills: Java\n"
                        "Work Experience\nSenior Java Developer, ABC Corp 2022-Present\n")
    assert b["experience_years"] >= 3.0
    assert b["explainable"]["aggregate"]["experience_fit"] == 100.0


def test_personal_project_is_not_professional_experience():
    _, b = _post(resume="Sam Lee\nSkills: Java\nProjects: Personal portfolio site\n")
    ed = b["explainable"]["aggregate"]["experience_detail"]
    assert ed["detected"] is False


def test_ranking_separation_preserved():
    strong = """Ravi Patel
Skills: Java, Spring Boot, React, SQL, REST API
Projects: CareerEdge full-stack app using Java, Spring Boot, PostgreSQL and React; built REST API endpoints.
Work Experience
Senior Java Developer, ABC Corp 2022-Present. Built and deployed Spring Boot microservices with React.
"""
    weak = "Sam Lee\nSkills: Excel\nProjects: Grocery billing spreadsheet\n"
    _, hi = _post(resume=strong)
    _, lo = _post(resume=weak)
    assert hi["final_score"] > lo["final_score"]
    assert hi["final_score"] >= 60


def test_internship_not_reported_as_zero_experience():
    _, b = _post()
    assert b["experience_years"] > 0
    ed = b["explainable"]["aggregate"]["experience_detail"]
    assert ed["detected"] is True
    assert ed["actual_years"] > 0


def test_missing_experience_is_reported_as_missing_not_zero_years():
    _, b = _post(resume="Jane Doe\nSkills: Java\nProjects: demo app\n")
    ed = b["explainable"]["aggregate"]["experience_detail"]
    assert ed["detected"] is False
    assert "not proof of zero" in ed["note"]


# ── R4: section attribution ────────────────────────────────────────────
def test_skills_and_projects_not_labelled_education():
    secs = parse_resume_sections(RESUME)
    assert "java" in secs["skills"].lower()
    assert "careeredge" in secs["projects"].lower()
    assert "java" not in secs["education"].lower()


def test_evidence_section_matches_real_source():
    _, b = _post()
    rows = {r["name"]: r for r in b["explainable"]["requirement_scores"]}
    assert rows["Java"]["evidence_section"] == "Projects"
    assert rows["Sql"]["evidence_section"] == "Skills"


def test_inline_skill_line_is_captured():
    secs = parse_resume_sections(RESUME)
    assert "SQL" in secs["skills"]


# ── R5: mandatory only when explicit ───────────────────────────────────
def test_preferred_skills_are_not_mandatory():
    reqs = {r["canonical_skill"]: r for r in extract_requirements(JD_JAVA)}
    assert reqs["kubernetes"]["mandatory"] is False
    assert reqs["docker"]["mandatory"] is False


def test_unheaded_jd_does_not_mark_everything_mandatory():
    reqs = extract_requirements("We need a Java developer. ASP.NET and Bootstrap helpful.")
    for r in reqs:
        if r["canonical_skill"] in ("asp.net", "bootstrap"):
            assert r["mandatory"] is False, r


def test_required_section_skills_are_mandatory():
    reqs = {r["canonical_skill"]: r for r in extract_requirements(JD_JAVA)}
    assert reqs["spring boot"]["mandatory"] is True
    assert reqs["spring boot"]["mandatory_explicit"] is True


def test_inferred_priorities_not_eligibility_failures():
    from services.eligibility_checker import check_eligibility
    reqs = extract_requirements("Java developer wanted. Communication is important.")
    out = check_eligibility(reqs, [], "Bachelors", 1, 0, {})
    for c in out["criteria"]:
        if "communication" in c["criterion"].lower():
            assert c["status"] != "VERIFIED_INELIGIBLE"


# ── R6: R_i arithmetic reproducible ────────────────────────────────────
def test_requirement_formula_reproducible_from_inputs():
    _, b = _post()
    for r in b["explainable"]["requirement_scores"]:
        fi = r["formula_inputs"]
        assert fi["K"] == r["keyword_match"]
        assert fi["M"] == r["semantic_score"]
        assert fi["P"] == r["evidence_strength"]
        calc = 100 * (fi["w_keyword"] * fi["K"] + fi["w_semantic"] * fi["M"]
                      + fi["w_evidence"] * fi["P"])
        assert abs(calc - r["requirement_score"]) <= 0.1
        assert all(0.0 <= fi[k] <= 1.0 for k in ("K", "M", "P"))


def test_evidence_passage_supports_matched_skill():
    _, b = _post()
    for r in b["explainable"]["requirement_scores"]:
        if r["evidence_strength"] > 0 and r["resume_evidence"]:
            assert r["canonical_skill"].split()[0].lower() in r["resume_evidence"].lower()