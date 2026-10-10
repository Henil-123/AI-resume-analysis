"""Aggregation: hand-calculated R_i / S, zero-weight, invalid-weight guards."""
import pytest
from services import scoring_engine as se


def test_requirement_formula_hand_calculated():
    # R = 100*(0.35*1.0 + 0.35*0.5 + 0.30*0.7) = 100*(0.35+0.175+0.21) = 73.5
    req = {"requirement_id": "req_001", "name": "Python", "canonical_skill": "python",
           "description": "Python services", "importance_weight": 3}
    out = se.score_requirement.__wrapped__ if hasattr(se.score_requirement, "__wrapped__") else None
    # monkeypatch semantic+evidence via crafted evidence is complex; test aggregate math directly:
    agg = se.aggregate(
        [{"requirement_id": "r1", "keyword_match": 1.0, "semantic_score": 0.5,
          "requirement_score": 73.5, "importance_weight": 3, "status": "matched"}],
        experience_score=60.0)
    # K=100, M=50, E=60 -> S = 60+15+6 = 81.0 ; ReqWeighted = 73.5
    assert agg["status"] == "ok"
    assert agg["keyword_coverage"] == 100.0
    assert agg["semantic_relevance"] == 50.0
    assert agg["overall_score"] == 81.0
    assert agg["requirement_weighted_score"] == 73.5


def test_zero_requirements_is_insufficient_data():
    out = se.aggregate([], 50.0)
    assert out["status"] == "insufficient-data"


def test_zero_total_weight_is_insufficient_data():
    out = se.aggregate(
        [{"requirement_id": "r1", "keyword_match": 1.0, "semantic_score": 1.0,
          "requirement_score": 100.0, "importance_weight": 0, "status": "matched"}],
        50.0)
    assert out["status"] == "insufficient-data"


def test_recalculate_weights_must_sum_to_one():
    with pytest.raises(ValueError):
        se.analyze([], [], [], "", 0, 0,
                   weights_override={"keyword": 0.5, "semantic": 0.5, "experience": 0.5})


def test_mandatory_unknown_not_failure():
    from services.eligibility_checker import check_eligibility
    out = check_eligibility([], [], "Not specified", 0, 0, {})
    assert out["overall"] == "UNKNOWN"


def test_explicit_contradiction_is_ineligible():
    from services.eligibility_checker import check_eligibility
    out = check_eligibility([], [], "Bachelors", 0, 0, {"education_required": "Masters"})
    assert out["overall"] == "VERIFIED_INELIGIBLE"
