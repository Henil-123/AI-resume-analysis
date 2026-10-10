"""Semantic-similarity calibration against human labels.

Spec F.8 asks to calibrate the semantic thresholds against human-labelled
examples. No such corpus existed in the repository, so this module ships a
small, explicitly-labelled seed set and a repeatable calibration report.

IMPORTANT, stated plainly: this seed set is SMALL and hand-written by the
developer. It is a smoke test of band ordering and a regression guard, NOT a
statistically validated calibration. `calibration_report` reports the sample
size so no caller can mistake it for a benchmark, and the docs say so too.

Each case pairs a requirement description with a resume passage and a human
label:
    match      - the passage genuinely demonstrates the required capability
    related    - topically related but a DIFFERENT, non-equivalent technology
    unrelated  - no meaningful relation

The expectation is ordinal: match > related > unrelated in cosine. That is the
property the thresholds must preserve, and it is what the tests assert.
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
LABELS_PATH = os.path.join(_HERE, "semantic_calibration.json")

# Human labels for the seed cases. Kept in code (not config) so a change to
# scoring_config.yaml cannot silently rewrite the labels.
SEED_CASES = [
    # (requirement, passage, human label)
    ("Orchestrate container workloads with Kubernetes and manage deployments",
     "Deployed the billing service to a Kubernetes cluster and wrote Helm charts "
     "for rolling deployments across three environments.",
     "match"),
    ("Orchestrate container workloads with Kubernetes and manage deployments",
     "Designed REST endpoints in Spring Boot that talk to a MySQL database.",
     "related"),
    ("Orchestrate container workloads with Kubernetes and manage deployments",
     "Managed a grocery inventory spreadsheet and reconciled monthly stock counts.",
     "unrelated"),
    ("Build responsive user interfaces with React and component-based design",
     "Built a React dashboard with reusable components and CSS modules for the "
     "checkout flow.",
     "match"),
    ("Build responsive user interfaces with React and component-based design",
     "Created server-side Jinja templates and CSS for an internal reporting page.",
     "related"),
    ("Build responsive user interfaces with React and component-based design",
     "Wrote unit tests for the payroll tax calculation module in Python.",
     "unrelated"),
    ("Write SQL queries and design relational schemas for reporting",
     "Designed normalized schemas and wrote complex SQL JOIN queries for monthly "
     "revenue reporting.",
     "match"),
    ("Write SQL queries and design relational schemas for reporting",
     "Stored application state in a Redis cache to reduce database round trips.",
     "related"),
    ("Write SQL queries and design relational schemas for reporting",
     "Coordinated a team sprint and presented the roadmap to stakeholders.",
     "unrelated"),
    ("Write automated unit and integration tests for backend services",
     "Added JUnit and Mockito integration tests covering the payment service "
     "endpoints, raising coverage to 85%.",
     "match"),
    ("Write automated unit and integration tests for backend services",
     "Performed manual exploratory testing of the checkout flow across browsers.",
     "related"),
    ("Write automated unit and integration tests for backend services",
     "Maintained the company wiki and onboarded two new engineering interns.",
     "unrelated"),
]


def seed_cases():
    return list(SEED_CASES)


def calibration_report(embedding_fn=None, cfg=None):
    """Score the seed cases and report band ordering + separation.

    `embedding_fn(texts) -> matrix` is injected so the caller controls the
    engine (and tests can stub it). Returns a dict that is safe to embed in
    the report; it never invents numbers when the model is unavailable.
    """
    try:
        if embedding_fn is None:
            from services import job_matcher as _jm
            embedding_fn = _jm._embed_batch
    except Exception:
        embedding_fn = None

    if embedding_fn is None:
        return {"available": False,
                "reason": "embedding engine unavailable",
                "sample_size": len(SEED_CASES),
                "disclaimer": ("No calibration was performed. Thresholds are "
                               "defaults, not validated against labels.")}

    try:
        from services.scoring_engine import transform_cosine, semantic_band, get_config
    except Exception as e:
        return {"available": False, "reason": str(e)[:160],
                "sample_size": len(SEED_CASES)}

    cfg = cfg or get_config()
    rows, failed = [], 0
    try:
        for req, passage, label in SEED_CASES:
            mat = embedding_fn([req, passage])
            sims = (mat @ mat[:1].T).ravel()
            raw = float(sims[1])
            norm, _ = transform_cosine(raw, cfg)
            rows.append({"requirement": req[:70], "label": label,
                         "raw_cosine": round(raw, 4), "normalized": norm,
                         "band": semantic_band(norm, cfg)["band"]})
    except Exception as e:
        return {"available": False, "reason": f"inference failed: {str(e)[:160]}",
                "sample_size": len(SEED_CASES)}

    def _mean(label):
        vals = [r["normalized"] for r in rows if r["label"] == label]
        return round(sum(vals) / len(vals), 4) if vals else None

    means = {lab: _mean(lab) for lab in ("match", "related", "unrelated")}
    ordering_ok = bool(
        means["match"] is not None and means["related"] is not None
        and means["unrelated"] is not None
        and means["match"] >= means["related"] >= means["unrelated"]
    )
    return {
        "available": True,
        "sample_size": len(rows),
        "group_mean_normalized": means,
        "ordering_match_ge_related_ge_unrelated": ordering_ok,
        "cases": rows,
        "disclaimer": ("Hand-written seed set of %d cases. This is a smoke test "
                       "of band ordering, NOT a statistically validated "
                       "calibration. Thresholds remain defaults."
                       % len(rows)),
    }


def load_labels(path=LABELS_PATH):
    """Load an external labelled corpus if one has been supplied."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception:
            return []
    return [(d["requirement"], d["passage"], d["label"])
            for d in data
            if d.get("requirement") and d.get("passage") and d.get("label")]