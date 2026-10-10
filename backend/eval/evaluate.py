"""Evaluation runner for the versioned semantic dataset (eval/dataset_v1.json).

Reports measured metrics only — never invented:
- group means + ordinal ordering (match >= related >= unrelated)
- pairwise accuracy per requirement (match>related, match>unrelated,
  related>unrelated)
- ranking quality per requirement group (P@1, NDCG@3 over match=2/related=1/
  unrelated=0 gains)
- binary P/R/F1 for `match` vs rest using the configured moderate threshold
  as the classifier cut (reported WITH the threshold, not as a tuned result)
- evidence-span hit rate (span substring present in the match passage)
- reproducibility (max abs diff across two runs) and latency

Usage:
    python -m eval.evaluate [--engine tfidf|bert] [--json]
    SEMANTIC_ENGINE=tfidf python -m eval.evaluate
"""
import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
DATASET = os.path.join(_HERE, "dataset_v1.json")
sys.path.insert(0, os.path.dirname(_HERE))


def load_dataset(path=DATASET):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("dataset_version", "?"), data.get("cases", [])


def _embed_all(texts, embedding_fn):
    import numpy as _np
    # One batch so bag-of-words engines share one vocabulary space.
    mat = embedding_fn(texts)
    return _np.asarray(mat, dtype="float32")


def evaluate(embedding_fn=None, engine_label=None, cases=None, version=None):
    import math
    import numpy as _np
    from services.scoring_engine import transform_cosine, get_config

    if cases is None:
        version, cases = load_dataset()
    cfg = get_config()
    if embedding_fn is None:
        from services import job_matcher as _jm
        embedding_fn = _jm._embed_batch
        engine_label = engine_label or _jm.get_embed_model_name() or "unknown"

    # Group passages by requirement text.
    groups = {}
    for c in cases:
        groups.setdefault(c["requirement"], []).append(c)

    texts = []
    index = {}
    for req, members in groups.items():
        for m in members:
            index[(req, m["id"])] = (len(texts), len(texts) + 1)
            texts.extend([req, m["passage"]])

    t0 = time.time()
    mat = _embed_all(texts, embedding_fn)
    encode_s = time.time() - t0

    rows = []
    for c in cases:
        i, j = index[(c["requirement"], c["id"])]
        raw = float(mat[i] @ mat[j])
        norm, _ = transform_cosine(raw, cfg)
        rows.append({**c, "raw_cosine": round(raw, 4), "normalized": norm})

    def mean(label):
        vals = [r["normalized"] for r in rows if r["label"] == label]
        return round(sum(vals) / len(vals), 4) if vals else None

    means = {lab: mean(lab) for lab in ("match", "related", "unrelated")}
    ordering = (means["match"] is not None and means["related"] is not None
                and means["unrelated"] is not None
                and means["match"] >= means["related"] >= means["unrelated"])

    # Pairwise accuracy within each requirement group.
    pairs = {"match>related": [], "match>unrelated": [], "related>unrelated": []}
    ndcg, p_at_1 = [], []
    for req, members in groups.items():
        by_label = {}
        for m in members:
            by_label.setdefault(m["label"], []).append(
                next(r["normalized"] for r in rows if r["id"] == m["id"]))
        mh = max(by_label.get("match", [0])) if by_label.get("match") else 0
        rh = max(by_label.get("related", [0])) if by_label.get("related") else 0
        uh = max(by_label.get("unrelated", [0])) if by_label.get("unrelated") else 0
        if "match" in by_label and "related" in by_label:
            pairs["match>related"].append(1.0 if mh > rh else 0.0)
        if "match" in by_label and "unrelated" in by_label:
            pairs["match>unrelated"].append(1.0 if mh > uh else 0.0)
        if "related" in by_label and "unrelated" in by_label:
            pairs["related>unrelated"].append(1.0 if rh > uh else 0.0)
        # Ranking over match=2 / related=1 / unrelated=0.
        gain = {"match": 2, "related": 1, "unrelated": 0}
        ranked = sorted(members,
                        key=lambda m: next(r["normalized"] for r in rows
                                           if r["id"] == m["id"]), reverse=True)
        rels = [gain[m["label"]] for m in ranked]
        dcg = sum(g / math.log2(i + 2) for i, g in enumerate(rels))
        ideal = sum(g / math.log2(i + 2)
                    for i, g in enumerate(sorted(rels, reverse=True)))
        ndcg.append(round(dcg / ideal, 4) if ideal else 1.0)
        p_at_1.append(1.0 if ranked and ranked[0]["label"] == "match" else 0.0)

    # Binary match-vs-rest at the configured moderate threshold.
    moderate = (cfg.get("semantic", {}) or {}).get("moderate_threshold", 0.5)
    tp = fp = fn = tn = 0
    for r in rows:
        pred = r["normalized"] >= moderate
        actual = r["label"] == "match"
        tp += pred and actual
        fp += pred and not actual
        fn += (not pred) and actual
        tn += (not pred) and (not actual)
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0

    # Evidence spans: the span must occur in its own passage (span honesty).
    spans = [c for c in cases if c.get("evidence_span")]
    span_hits = sum(1 for c in spans if c["evidence_span"] in c["passage"])

    # Reproducibility: second run, max abs diff of normalized scores.
    mat2 = _embed_all(texts, embedding_fn)
    max_diff = 0.0
    for c in cases:
        i, j = index[(c["requirement"], c["id"])]
        raw2 = float(mat2[i] @ mat2[j])
        norm2, _ = transform_cosine(raw2, cfg)
        prev = next(r["normalized"] for r in rows if r["id"] == c["id"])
        max_diff = max(max_diff, abs(norm2 - prev))

    avg = lambda xs: round(sum(xs) / len(xs), 4) if xs else None
    return {
        "engine": engine_label,
        "dataset_version": version,
        "sample_count": len(rows),
        "group_mean_normalized": means,
        "ordering_match_ge_related_ge_unrelated": ordering,
        "pairwise_accuracy": {k: avg(v) for k, v in pairs.items()},
        "ndcg_mean": avg(ndcg),
        "precision_at_1_mean": avg(p_at_1),
        "match_vs_rest_at_moderate_threshold": {
            "threshold": moderate, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(prec, 4), "recall": round(rec, 4),
            "f1": round(f1, 4)},
        "evidence_span_hit_rate": (round(span_hits / len(spans), 4) if spans else None),
        "evidence_spans_checked": len(spans),
        "reproducibility_max_abs_diff": round(float(max_diff), 6),
        "latency_s": round(encode_s, 3),
        "disclaimer": ("Synthetic hand-written set v%s (n=%d). Regression and "
                       "comparison signal, NOT a validated benchmark."
                       % (version, len(rows))),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["tfidf", "bert"], default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.engine == "tfidf":
        os.environ["SEMANTIC_ENGINE"] = "tfidf"
    elif args.engine == "bert":
        os.environ.pop("SEMANTIC_ENGINE", None)
    rep = evaluate()
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print(f"engine:  {rep['engine']}")
        print(f"dataset: v{rep['dataset_version']} n={rep['sample_count']}")
        print(f"means:   {rep['group_mean_normalized']} ordering={rep['ordering_match_ge_related_ge_unrelated']}")
        print(f"pairwise:{rep['pairwise_accuracy']}")
        print(f"rank:    ndcg={rep['ndcg_mean']} p@1={rep['precision_at_1_mean']}")
        m = rep["match_vs_rest_at_moderate_threshold"]
        print(f"match-vs-rest@{m['threshold']}: P={m['precision']} R={m['recall']} F1={m['f1']} "
              f"(tp={m['tp']} fp={m['fp']} fn={m['fn']} tn={m['tn']})")
        print(f"spans:   {rep['evidence_span_hit_rate']} over {rep['evidence_spans_checked']}")
        print(f"repro:   max_abs_diff={rep['reproducibility_max_abs_diff']} latency={rep['latency_s']}s")
        print(rep["disclaimer"])


if __name__ == "__main__":
    main()
