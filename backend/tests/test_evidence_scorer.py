"""Evidence rubric: implementation 1.0 / practical 0.7 / mention 0.3 / none 0.0."""
from services import scoring_engine as se


def _req(skill="docker"):
    return {"requirement_id": "req_001", "name": skill, "canonical_skill": skill,
            "description": skill, "importance_weight": 3}


def _ev(etype, text="docker", neg=False, planned=False, dup=False):
    return {"canonical_skill": "docker", "original_text": text, "section": "Projects",
            "page_number": 1, "evidence_type": etype, "polarity": "negative" if neg else "positive",
            "is_negated": neg, "is_planned": planned, "is_duplicate": dup}


def test_project_implementation_is_full():
    p, _, _ = se.score_evidence(_req(), [_ev("project_implementation", "Built and deployed a Dockerized API")])
    assert p == 1.0


def test_work_experience_is_practical():
    p, _, _ = se.score_evidence(_req(), [_ev("work_experience", "Used Docker daily")])
    assert p == 0.7


def test_skills_section_only_is_mention():
    p, _, _ = se.score_evidence(_req(), [_ev("skills_section", "Docker")])
    assert p == 0.3


def test_no_evidence_is_zero():
    p, _, _ = se.score_evidence(_req(), [])
    assert p == 0.0


def test_negated_scores_zero():
    p, _, _ = se.score_evidence(_req(), [_ev("work_experience", "No Docker experience", neg=True)])
    assert p == 0.0


def test_planned_work_capped():
    p, _, _ = se.score_evidence(
        _req(), [_ev("project_implementation", "Planning to learn Docker", planned=True)])
    assert p <= 0.3


def test_duplicate_does_not_inflate():
    dup = _ev("project_implementation", "Built Docker API", dup=True)
    single = _ev("skills_section", "Docker")
    p, _, best = se.score_evidence(_req(), [dup, single])
    assert best.get("is_duplicate") is not True
