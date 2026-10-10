"""Semantic evaluation framework: dataset integrity + honest reporting."""
import json
import os

os.environ["SEMANTIC_ENGINE"] = "tfidf"

from eval.evaluate import evaluate, load_dataset


def test_dataset_loads_versioned_and_sized():
    version, cases = load_dataset()
    assert version == "1.0.0"
    assert len(cases) >= 30, "evaluation set must exceed the old 12-case seed"
    assert len({c["id"] for c in cases}) == len(cases), "ids must be unique"


def test_dataset_labels_and_kinds_valid():
    _, cases = load_dataset()
    for c in cases:
        assert c["label"] in ("match", "related", "unrelated"), c["id"]
        assert c["requirement_kind"] in ("explicit", "inferred", "irrelevant"), c["id"]
        assert isinstance(c["mandatory"], bool), c["id"]
        assert c["requirement"] and c["passage"], c["id"]


def test_dataset_contains_no_pii():
    blob = json.dumps(load_dataset()[1]).lower()
    for marker in ("@gmail", "@yahoo", "@example.com", "+91", "aadhaar", "passport"):
        assert marker not in blob, f"dataset must not contain {marker}"


def test_evidence_spans_are_substrings():
    _, cases = load_dataset()
    spans = [c for c in cases if c.get("evidence_span")]
    assert spans, "match cases must carry evidence spans"
    for c in spans:
        assert c["evidence_span"] in c["passage"], c["id"]


def test_cross_role_contamination_cases_present():
    _, cases = load_dataset()
    kinds = {(c["requirement_kind"], c["label"]) for c in cases}
    assert ("irrelevant", "unrelated") in kinds


def test_evaluate_reports_honestly_with_stub_engine():
    import numpy as _np

    def stub(texts):
        # Deterministic lexical-ish engine for framework testing only.
        rng = _np.random.RandomState(7)
        mat = rng.rand(len(texts), 8).astype("float32")
        mat /= (_np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
        return mat

    rep = evaluate(embedding_fn=stub, engine_label="stub")
    assert rep["sample_count"] >= 30
    assert "NOT a validated benchmark" in rep["disclaimer"]
    assert set(rep["pairwise_accuracy"]) == {"match>related", "match>unrelated",
                                             "related>unrelated"}
    assert 0.0 <= rep["ndcg_mean"] <= 1.0
    assert 0.0 <= rep["precision_at_1_mean"] <= 1.0


def test_real_engine_result_is_measured_not_claimed():
    rep = evaluate()
    assert rep["available"] if "available" in rep else True
    m = rep["group_mean_normalized"]
    assert rep["ordering_match_ge_related_ge_unrelated"] == (
        m["match"] >= m["related"] >= m["unrelated"])
    assert rep["reproducibility_max_abs_diff"] == 0.0
