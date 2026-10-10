"""Semantic matching: related > unrelated; failures never fabricated."""
from services import scoring_engine as se


def _req(skill, desc):
    return {"requirement_id": "req_001", "name": skill, "canonical_skill": skill,
            "description": desc, "importance_weight": 3}


def test_related_beats_unrelated():
    req = _req("postgresql", "Design normalized relational schemas in PostgreSQL")
    rel = [{"canonical_skill": "postgresql",
            "original_text": "Designed a normalized relational schema and integrated PostgreSQL",
            "section": "Projects", "page_number": 1, "evidence_type": "project_implementation",
            "polarity": "positive", "is_negated": False, "is_planned": False,
            "is_duplicate": False}]
    unr = [{"canonical_skill": "postgresql",
            "original_text": "Baked sourdough bread on weekends",
            "section": "Other", "page_number": 1, "evidence_type": "general_mention",
            "polarity": "positive", "is_negated": False, "is_planned": False,
            "is_duplicate": False}]
    r_rel = se.score_semantic(req, rel, "PostgreSQL schema design project")
    r_unr = se.score_semantic(req, unr, "Baked sourdough bread")
    assert r_rel["model"] is not None
    assert r_rel["raw_cosine"] > r_unr["raw_cosine"]
    assert 0.0 <= r_rel["normalized"] <= 1.0


def test_transform_reports_both_raw_and_normalized():
    req = _req("python", "Write Python services")
    out = se.score_semantic(req, [], "Python developer with Flask experience")
    assert "raw_cosine" in out and "normalized" in out
    assert out["model"] is not None  # real inference, tfidf engine in tests


def test_empty_resume_never_fabricates():
    req = _req("python", "Write Python services")
    out = se.score_semantic(req, [], "")
    assert out["normalized"] == 0.0
