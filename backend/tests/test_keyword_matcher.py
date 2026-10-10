"""Keyword matching: exact / alias / missing / false-positive / negation."""
from services import scoring_engine as se
from services.evidence_extractor import extract_evidence


def _req(skill):
    return {"requirement_id": "req_001", "name": skill, "canonical_skill": skill,
            "description": skill, "importance_weight": 3}


def test_exact_match_scores_one():
    k, _ = se.score_keyword(_req("python"), ["python", "sql"])
    assert k == 1.0


def test_alias_match_scores_one():
    # "postgres" normalizes to "postgresql" -> explicit match, not partial
    k, _ = se.score_keyword(_req("postgresql"), ["postgres"])
    assert k == 1.0


def test_missing_scores_zero():
    k, _ = se.score_keyword(_req("kubernetes"), ["docker"])
    assert k == 0.0


def test_no_substring_false_positive():
    # "go" must not match inside "django"; matching is canonical-exact
    k, _ = se.score_keyword(_req("go"), ["django"])
    assert k == 0.0
    k2, _ = se.score_keyword(_req("c"), ["c++"])
    assert k2 == 0.0


def test_fuzzy_variant_partial_credit():
    k, why = se.score_keyword(_req("python"),
                              ["pythoon"],
                              {"python": {"found_as": "pythoon", "ratio": 0.92}})
    assert k == 0.7


def test_negated_skill_detected_as_negative():
    parsed = {"raw_text": "Skills: Python, Docker\nExperience: I have no experience in Kubernetes.",
              "pages": [(1, "Skills: Python")] }
    ev = extract_evidence(parsed, ["kubernetes"])
    assert ev and ev[0]["is_negated"] is True
    assert ev[0]["polarity"] == "negative"
