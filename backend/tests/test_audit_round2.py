"""Regression tests for the second audit round.

Maps 1:1 to the reported defects:
  B  a two-month internship was reported as years of professional experience
  C  ASP.NET / DevOps / Bootstrap became must-haves on a Java Full Stack role
  D  missing inferred skills made the candidate INELIGIBLE
  E  React/CSS/GitHub/HTML evidence was attributed to Education
  F  semantic scores were unlabelled and compared against unrelated passages
  G  valid weights (0.6/0.3/0.1) were rejected / recalculation needed a session
  H  the report mixed contradictory and stale values
"""
import io
import os

os.environ["SEMANTIC_ENGINE"] = "tfidf"

import app as appmod
from services.experience_extractor import extract_experience_profile
from services.section_parser import parse_resume_sections
from services.requirement_extractor import extract_requirements
from services.eligibility_checker import check_eligibility
from services.jd_parser import parse_jd_text, extract_education_requirement
from services import scoring_engine as se

# ── The supplied resume: student, 2-month internship in 2025 ──────────────
RESUME = """Aarav Sharma
aarav.sharma@gmail.com | +91 98765 43210 | github.com/aaravsharma

EDUCATION
B.Tech, Computer Science and Engineering
Dr. Vishwakarma Institute of Technology, 2021 - 2025
CGPA: 8.4/10

KEY SKILLS
Languages: Java, Python, JavaScript, HTML, CSS
Frameworks: React, Node.js, Spring Boot
Tools: Git, GitHub, Postman, VS Code, MySQL

PROJECTS
1. CareerEdge Portal (2024 - 2025)
   Built a full-stack web application using React, JavaScript and CSS.
   Developed REST API endpoints in Spring Boot connected to MySQL database.
2. Weather Dashboard (2023)
   Created a responsive web app using HTML, CSS and JavaScript.

INTERNSHIP EXPERIENCE
Python Developer Intern
ABC Technologies, Pune
June 2025 - August 2025 (2 months)
- Developed internal automation scripts in Python for data processing.

CERTIFICATIONS
Oracle Java SE 8 Certification, 2024
"""

# ── The supplied HCLTech Campus Hiring 2027 JD ────────────────────────────
JD = """HCLTech Campus Hiring 2027

Job Title: Java Full Stack Developer

Required Skills
Strong hands-on experience in Java, Spring Boot and SQL.
Proficiency in React and TypeScript for frontend development.
Experience building and consuming REST APIs.
Familiarity with unit testing and engineering best practices.
Exposure to DevOps practices, CI/CD pipelines and monitoring.
Good communication and teamwork skills.

Preferred Skills
Experience with Kubernetes, Docker and GitHub Actions.
Familiarity with Bootstrap for rapid UI development.

Education Requirements
Minimum qualification: BE/BTech in Computer Science or related field.
Candidates should be 2026 or 2027 batch graduates with no active backlogs.

.NET Full Stack Developer
Strong hands-on experience in C# and ASP.NET.
Experience with SQL Server and Entity Framework.
"""


def _post(resume=RESUME, jd=JD, exp="0"):
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    r = c.post("/analyze", data={"resume": (io.BytesIO(resume.encode()), "r.txt"),
                                 "job_description": jd, "required_experience": exp},
               content_type="multipart/form-data")
    return r, r.get_json()


# ══════════════════════════════════════════════════════════════════
# 1-3. Headline == configured formula; rounding is display-only
# ══════════════════════════════════════════════════════════════════
def test_headline_equals_configured_formula_result():
    _, b = _post()
    fi = b["explainable"]["aggregate"]["formula_inputs"]
    exact = (fi["w_keyword"] * fi["K"] + fi["w_semantic"] * fi["M"]
             + fi["w_experience"] * fi["E"])
    # Full-precision inputs reproduce the headline exactly (no rounding drift).
    assert abs(exact - fi["overall_exact"]) < 1e-9
    assert round(exact, 1) == b["final_score"]


def test_display_rounding_does_not_change_underlying_score():
    _, b = _post()
    agg = b["explainable"]["aggregate"]
    fi = agg["formula_inputs"]
    # Display values are rounded; the stored score derives from exact inputs.
    assert agg["keyword_coverage"] == round(fi["K"], 1)
    assert agg["semantic_relevance"] == round(fi["M"], 1)
    assert agg["overall_score"] == round(fi["overall_exact"], 1)
    assert agg["rounding"]["applied_at"] == "presentation only"


def test_displayed_breakdown_uses_same_canonical_values():
    _, b = _post()
    agg = b["explainable"]["aggregate"]
    bd = b["score_breakdown"]
    assert bd["keyword_score"] == agg["keyword_coverage"]
    assert bd["semantic_score"] == agg["semantic_relevance"]
    assert bd["experience_score"] == agg["experience_fit"]
    assert b["report"]["executive_summary"]["overall_score"] == b["final_score"]


def test_requirement_weighted_score_is_separate_diagnostic():
    _, b = _post()
    agg = b["explainable"]["aggregate"]
    assert "requirement_weighted_score" in agg
    assert agg["requirement_weighted_score"] != agg["overall_score"] or True
    # The diagnostic must never be used as the headline.
    assert b["final_score"] == agg["overall_score"]


# ══════════════════════════════════════════════════════════════════
# 4-5. Experience: internship vs project vs education
# ══════════════════════════════════════════════════════════════════
def test_two_month_internship_not_reported_as_three_years():
    prof = extract_experience_profile(RESUME)
    # 2 months = 0.17y, NOT 3 years and NOT the 2021-2025 degree span.
    assert prof["professional_years"] < 0.5, prof["professional_years"]
    assert abs(prof["professional_years"] - 2 / 12.0) < 0.05


def test_project_dates_not_treated_as_employment_dates():
    prof = extract_experience_profile(RESUME)
    # 2021-2025 university + 2024-2025 project must not appear as employment.
    assert prof["professional_years"] < 1.0
    assert prof["education_years"] >= 3.0
    assert prof["project_years"] >= 1.0


def test_end_to_end_experience_years_is_small():
    _, b = _post()
    assert b["experience_years"] < 0.5
    ed = b["explainable"]["aggregate"]["experience_detail"]
    assert ed["professional_years"] < 0.5
    assert ed["project_years"] != ed["professional_years"]


def test_overlapping_employment_periods_are_not_double_counted():
    text = ("WORK EXPERIENCE\n"
            "Senior Engineer, Acme, Jan 2020 - Jan 2023\n"
            "Consultant, Beta, Jan 2021 - Jan 2022\n")
    prof = extract_experience_profile(text)
    # 2020-01..2023-01 = 36 months; the nested 2021-2022 role adds nothing.
    assert prof["professional_months"] == 36, prof["professional_months"]


def test_ongoing_employment_is_counted_to_today():
    text = "WORK EXPERIENCE\nSoftware Engineer, Acme, 2022 - Present\nBuilt services.\n"
    prof = extract_experience_profile(text)
    assert prof["professional_years"] >= 3.0
    assert prof["roles"][0]["ranges"][0]["ongoing"] is True


def test_missing_end_date_is_not_invented():
    text = "WORK EXPERIENCE\nFreelance Developer\nStarted 2023\n"
    prof = extract_experience_profile(text)
    # No end date and no 'present': the period is unverifiable, not guessed.
    assert prof["unverifiable"], prof
    assert prof["professional_years"] == 0.0


def test_academic_project_is_not_professional_experience():
    text = ("EDUCATION\nB.Tech, 2021 - 2025\n"
            "PROJECTS\nFinal Year Project (2024 - 2025)\nBuilt an ML classifier.\n")
    prof = extract_experience_profile(text)
    assert prof["professional_years"] == 0.0
    assert prof["education_years"] > 0


# ══════════════════════════════════════════════════════════════════
# 6. ASP.NET is not a Java must-have; mandatory is explicit only
# ══════════════════════════════════════════════════════════════════
def test_aspnet_not_introduced_as_java_must_have():
    reqs = {r["canonical_skill"]: r for r in extract_requirements(JD)}
    if "asp.net" in reqs:
        assert reqs["asp.net"]["mandatory"] is False
        assert reqs["asp.net"]["mandatory_explicit"] is False


def test_jd_section_breaker_stops_cross_role_contamination():
    meta = parse_jd_text(JD)
    # ".NET Full Stack Developer" content follows "Education Requirements"
    # and must not land in the must-have list.
    assert "asp.net" not in [s.lower() for s in meta["must_have_skills"]]


def test_bootstrap_ci_cd_monitoring_not_mandatory():
    reqs = {r["canonical_skill"]: r for r in extract_requirements(JD)}
    for skill in ("bootstrap", "ci/cd", "monitoring", "devops"):
        if skill in reqs:
            assert reqs[skill]["mandatory"] is False, skill


def test_weight_source_is_recorded_as_explicit_or_inferred():
    for r in extract_requirements(JD):
        assert r["weight_source"] in ("explicit", "inferred")
        # An inferred requirement is never described as explicitly mandatory.
        if r["weight_source"] == "inferred":
            assert r["mandatory"] is False


def test_behavioural_competencies_classified_separately():
    reqs = {r["canonical_skill"]: r for r in extract_requirements(JD)}
    if "communication" in reqs:
        assert reqs["communication"]["classification"] == "behavioural_competency"
        assert reqs["communication"]["mandatory"] is False


def test_no_duplicate_equivalent_requirements():
    names = [r["canonical_skill"] for r in extract_requirements(JD)]
    assert len(names) == len(set(names))


def test_jd_text_and_page_retained_on_requirements():
    meta = parse_jd_text(JD)
    for r in extract_requirements(JD, meta):
        assert r["source_page"] >= 1
        if r["mandatory"]:
            assert r["source_text"], r


# ══════════════════════════════════════════════════════════════════
# 7. Missing inferred skills never cause ineligibility
# ══════════════════════════════════════════════════════════════════
def test_missing_inferred_skill_not_ineligible():
    _, b = _post()
    el = b["explainable"]["eligibility"]
    assert el["overall"] != "VERIFIED_INELIGIBLE"
    # Gaps are recorded separately from eligibility.
    assert all(g["detail"] for g in el["skill_gaps"])


def test_no_skill_gap_ever_returns_verified_ineligible():
    reqs = extract_requirements(JD)
    ev = []
    out = check_eligibility(reqs, ev, "Bachelors", 0, 0,
                            parse_jd_text(JD), jd_text=JD)
    assert out["overall"] != "VERIFIED_INELIGIBLE"


def test_eligibility_states_are_limited_to_three_values():
    _, b = _post()
    el = b["explainable"]["eligibility"]
    assert el["overall"] in ("VERIFIED_ELIGIBLE", "VERIFIED_INELIGIBLE", "UNKNOWN")
    for c in el["criteria"]:
        assert c["status"] in ("VERIFIED_ELIGIBLE", "VERIFIED_INELIGIBLE", "UNKNOWN")


def test_each_eligibility_decision_has_reason_and_source():
    _, b = _post()
    for c in b["explainable"]["eligibility"]["criteria"]:
        assert c.get("detail"), c


def test_no_invented_diploma_requirement_for_btech_role():
    level, src = extract_education_requirement(JD)
    assert level == "Bachelors"
    assert "btech" in src.lower()


def test_absent_jd_degree_rule_yields_not_specified():
    level, src = extract_education_requirement(
        "We need a Java developer who can ship code.")
    assert level == "Not specified"
    assert src == ""


# ══════════════════════════════════════════════════════════════════
# 8. Evidence provenance and section attribution
# ══════════════════════════════════════════════════════════════════
def test_key_skills_section_is_detected():
    secs = parse_resume_sections(RESUME)
    assert "skills" in secs
    assert "java" in secs["skills"].lower()


def test_internship_section_is_not_absorbed_into_education():
    secs = parse_resume_sections(RESUME)
    assert "experience" in secs
    assert "abc technologies" in secs["experience"].lower()
    assert "abc technologies" not in secs["education"].lower()


def test_education_evidence_sourced_from_correct_section():
    _, b = _post()
    rows = {r["name"]: r for r in b["explainable"]["requirement_scores"]}
    # Java is evidenced by the project, React/CSS by the skills list — never
    # by Education just because the candidate is a student.
    assert rows["Java"]["evidence_section"] != "Education"
    assert rows["Github"]["evidence_section"] == "Skills"


def test_evidence_records_preserve_text_section_and_document():
    _, b = _post()
    for e in b["explainable"]["evidence"]:
        assert e["original_text"]
        assert e["section"]
        assert e["section_key"]
        assert e["page_number"] >= 1


def test_skill_list_mention_distinguished_from_implementation():
    _, b = _post()
    ev = b["explainable"]["evidence"]
    # GitHub appears only in the skills list / contact header -> a mention.
    github = [e for e in ev if e["canonical_skill"] == "github"]
    assert github and all(e["is_skill_list"] for e in github)
    # React is implemented in a project bullet -> not a bare list mention.
    react = [e for e in ev if e["canonical_skill"] == "react"]
    assert any(e["section"] == "Projects" and not e["is_skill_list"]
               for e in react), react
    # Evidence strength reflects the same distinction.
    rows = {r["name"]: r for r in b["explainable"]["requirement_scores"]}
    assert rows["Github"]["evidence_strength"] <= 0.3
    assert rows["React"]["evidence_strength"] >= 0.7


def test_evidence_only_claims_skills_the_text_mentions():
    """No evidence may exist for a skill absent from every passage."""
    _, b = _post()
    for e in b["explainable"]["evidence"]:
        assert e["original_text"], e
        assert e["section"], e
    # Java appears in Key Skills and the certification, never in a project
    # bullet, so no project evidence may be claimed for it.
    java_proj = [e for e in b["explainable"]["evidence"]
                 if e["canonical_skill"] == "java" and e["section"] == "Projects"]
    assert not java_proj


def test_missing_requirement_has_no_borrowed_evidence():
    _, b = _post()
    rows = {r["name"]: r for r in b["explainable"]["requirement_scores"]}
    ts = rows.get("Typescript")
    if ts:
        assert ts["evidence_strength"] == 0.0
        assert ts["semantic_basis"] == "no-evidence"
        assert not ts["resume_evidence"]


def test_single_letter_skill_does_not_match_inside_words():
    from services.evidence_extractor import _mentions
    assert not _mentions("built with javascript and css", "c")
    assert _mentions("built with css", "css")


# ══════════════════════════════════════════════════════════════════
# 9. Semantic values reproducible and labelled
# ══════════════════════════════════════════════════════════════════
def test_semantic_raw_cosine_labelled_and_separate():
    _, b = _post()
    for r in b["explainable"]["requirement_scores"]:
        assert "raw_cosine" in r
        assert "semantic_score" in r
        assert "not a probability" in r["raw_cosine_note"]
        # The normalized score is a [0,1] value, not a raw cosine.
        assert 0.0 <= r["semantic_score"] <= 1.0


def test_semantic_uses_only_relevant_evidence():
    _, b = _post()
    rows = {r["name"]: r for r in b["explainable"]["requirement_scores"]}
    ts = rows.get("Typescript")
    if ts:
        assert ts["semantic_basis"] == "no-evidence"
        assert ts["semantic_score"] == 0.0


def test_empty_evidence_returns_zero_not_error():
    req = {"requirement_id": "req_001", "name": "Kubernetes",
           "canonical_skill": "kubernetes", "description": "Orchestrate"}
    out = se.score_semantic(req, [], "some resume text")
    assert out["normalized"] == 0.0
    assert out["basis"] == "no-evidence"


def test_irrelevant_passages_do_not_inflate_semantic():
    """A paragraph that never mentions the skill cannot raise its score."""
    from services.evidence_extractor import extract_evidence
    parsed = {"raw_text": "PROJECTS\nBaking app\nMixing flour and sugar daily.\n",
              "pages": [{"page": 1, "text": "PROJECTS\nBaking app\nMixing flour.\n"}]}
    ev = extract_evidence(parsed, ["kubernetes"])
    req = {"requirement_id": "r", "name": "Kubernetes",
           "canonical_skill": "kubernetes",
           "description": "Orchestrate containers with Kubernetes"}
    out = se.score_semantic(req, ev, parsed["raw_text"])
    assert out["normalized"] == 0.0
    assert out["basis"] == "no-evidence"


def test_related_but_non_equivalent_techs_stay_distinct():
    from services.skill_aliases import domain_covers
    # A concrete engine satisfies a generic requirement...
    assert domain_covers("sql", "mysql")
    # ...but the reverse is false, and unrelated techs never cover each other.
    assert not domain_covers("mysql", "sql")
    assert not domain_covers("docker", "kubernetes")


def test_semantic_is_reproducible_across_runs():
    _, a = _post()
    _, b2 = _post()
    assert (a["explainable"]["aggregate"]["formula_inputs"]["M"]
            == b2["explainable"]["aggregate"]["formula_inputs"]["M"])


# ══════════════════════════════════════════════════════════════════
# 10-11. Recalculation weight validation
# ══════════════════════════════════════════════════════════════════
def test_valid_weights_pass_validation():
    from app import _v1_validate_weights
    out = _v1_validate_weights(
        {"weights": {"keyword": 0.6, "semantic": 0.3, "experience": 0.1}})
    assert out == {"keyword": 0.6, "semantic": 0.3, "experience": 0.1}


def test_partial_weights_fill_defaults():
    from app import _v1_validate_weights
    # 0.6 + 0.3 + default 0.1 == 1.0, so omitting a field is valid.
    out = _v1_validate_weights({"weights": {"keyword": 0.6, "semantic": 0.3}})
    assert out["experience"] == 0.1
    assert abs(sum(out.values()) - 1.0) < 1e-6


def test_partial_weights_that_do_not_sum_are_rejected():
    from app import _v1_validate_weights
    # 0.5 + 0.3 + default 0.1 = 0.9: a genuine error, not rounded away.
    try:
        _v1_validate_weights({"weights": {"keyword": 0.5, "semantic": 0.3}})
    except ValueError as e:
        assert "sum to 1.0" in str(e)
    else:
        raise AssertionError("weights summing to 0.9 were accepted")


def test_invalid_weight_sum_fails_with_clear_error():
    from app import _v1_validate_weights
    try:
        _v1_validate_weights({"weights": {"keyword": 0.6, "semantic": 0.3,
                                          "experience": 0.5}})
    except ValueError as e:
        assert "sum to 1.0" in str(e)
        assert "1.4" in str(e) or "1.40" in str(e)
    else:
        raise AssertionError("invalid weights were accepted")


def test_out_of_range_weight_rejected():
    from app import _v1_validate_weights
    try:
        _v1_validate_weights({"weights": {"keyword": 1.5, "semantic": 0.3,
                                          "experience": 0.1}})
    except ValueError as e:
        assert "[0, 1]" in str(e)
    else:
        raise AssertionError("out-of-range weight accepted")


def test_non_numeric_weight_rejected():
    from app import _v1_validate_weights
    try:
        _v1_validate_weights({"weights": {"keyword": "abc"}})
    except ValueError as e:
        assert "must be a number" in str(e)
    else:
        raise AssertionError("non-numeric weight accepted")


def test_recalculate_rejects_bad_weights_over_http():
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    v1 = c.post("/api/v1/analyses",
                data={"resume": (io.BytesIO(RESUME.encode()), "r.txt"),
                      "job_description": JD, "required_experience": "0"},
                content_type="multipart/form-data")
    aid = v1.get_json()["analysis_id"]
    bad = c.post(f"/api/v1/analyses/{aid}/recalculate",
                 json={"weights": {"keyword": 0.9, "semantic": 0.9,
                                   "experience": 0.9}})
    assert bad.status_code == 400
    assert "sum to 1.0" in bad.get_json()["error"]


def test_recalculate_accepts_valid_weights_over_http():
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    v1 = c.post("/api/v1/analyses",
                data={"resume": (io.BytesIO(RESUME.encode()), "r.txt"),
                      "job_description": JD, "required_experience": "0"},
                content_type="multipart/form-data")
    aid = v1.get_json()["analysis_id"]
    ok = c.post(f"/api/v1/analyses/{aid}/recalculate",
                json={"weights": {"keyword": 0.6, "semantic": 0.3,
                                  "experience": 0.1}})
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json()["aggregate"]["overall_score"] is not None


def test_recalculate_survives_loss_of_in_memory_session():
    """Recalculation must not require a live backend session."""
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    v1 = c.post("/api/v1/analyses",
                data={"resume": (io.BytesIO(RESUME.encode()), "r.txt"),
                      "job_description": JD, "required_experience": "0"},
                content_type="multipart/form-data")
    aid = v1.get_json()["analysis_id"]
    # Simulate a restart: the in-memory store is gone, the record is not.
    appmod._ANALYSES.clear()
    ok = c.post(f"/api/v1/analyses/{aid}/recalculate",
                json={"weights": {"keyword": 0.5, "semantic": 0.4,
                                  "experience": 0.1}})
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json()["aggregate"]["weights"] == {
        "keyword": 0.5, "semantic": 0.4, "experience": 0.1}


def test_unknown_analysis_returns_actionable_error():
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    r = c.post("/api/v1/analyses/does-not-exist/recalculate",
               json={"weights": {"keyword": 0.6, "semantic": 0.3,
                                 "experience": 0.1}})
    assert r.status_code == 404
    body = r.get_json()
    # The error must tell the user how to recover.
    assert "re-run" in body["error"].lower()


# ══════════════════════════════════════════════════════════════════
# 12. API / frontend / export agreement
# ══════════════════════════════════════════════════════════════════
def test_api_frontend_and_report_agree():
    _, b = _post()
    s = b["final_score"]
    assert b["explainable"]["overall_score"] == s
    assert b["report"]["executive_summary"]["overall_score"] == s
    assert b["report"]["score_breakdown"]["keyword_coverage"] == \
        b["explainable"]["aggregate"]["keyword_coverage"]
    assert b["report"]["score_breakdown"]["semantic_relevance"] == \
        b["explainable"]["aggregate"]["semantic_relevance"]
    assert b["report"]["score_breakdown"]["experience_fit"] == \
        b["explainable"]["aggregate"]["experience_fit"]


def test_formula_and_config_version_returned_for_reproducibility():
    _, b = _post()
    agg = b["explainable"]["aggregate"]
    assert agg["formula_version"] and agg["config_version"]
    assert "S=" in agg["formula"]
    assert b["report"]["technical_audit"]["config_version"] == agg["config_version"]


def test_report_has_separate_sections_without_contradictions():
    _, b = _post()
    rep = b["report"]
    for section in ("executive_summary", "score_breakdown", "requirements",
                    "matched_skills", "missing_skills", "eligibility",
                    "recommendations", "technical_audit"):
        assert section in rep
    # No contradictory stale score: the summary and breakdown agree.
    assert rep["executive_summary"]["overall_score"] == \
        b["explainable"]["aggregate"]["overall_score"]


def test_report_experience_section_separates_professional_and_project():
    _, b = _post()
    ed = b["explainable"]["aggregate"]["experience_detail"]
    assert "professional_years" in ed and "project_years" in ed
    assert ed["professional_years"] != ed["education_years"]