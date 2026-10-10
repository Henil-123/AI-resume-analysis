# Semantic evaluation

Dataset: `backend/eval/dataset_v1.json` (**v1.0.0, n=36**, synthetic,
hand-written, no real candidate data). Runner: `backend/eval/evaluate.py`.

Each case carries `requirement_kind` (explicit/inferred/irrelevant),
`mandatory`, an `evidence_span` for match cases, and notes. The runner
reports group means + ordinal ordering, pairwise accuracy per requirement,
ranking quality (NDCG, P@1 over match=2/related=1/unrelated=0),
binary match-vs-rest P/R/F1 at the configured moderate threshold,
evidence-span hit rate, reproducibility (two runs, max abs diff), and latency.

Run: `cd backend && python -m eval.evaluate --engine [tfidf|bert]`
(or `--json` for machine output).

## Measured results (2026-10-10, this repo, thresholds 0.75/0.50/0.25)

| Metric | `BAAI/bge-small-en-v1.5` | `tfidf-cosine (light)` |
|---|---|---|
| Group means (match / related / unrelated) | 0.7000 / 0.6409 / 0.4888 | 0.1686 / 0.0340 / 0.0536 |
| Ordering match ≥ related ≥ unrelated | **True** | **False** |
| Pairwise match>related / match>unrelated / related>unrelated | 0.80 / 1.00 / 1.00 | 0.80 / 0.70 / 0.30 |
| NDCG (mean) | 0.9713 | 0.9024 |
| Precision@1 (mean) | 0.8182 | 0.7273 |
| Match-vs-rest @0.50: P / R / F1 (tp/fp/fn/tn) | 0.40 / 1.00 / 0.5714 (12/18/0/6) | 0.00 / 0.00 / 0.0000 (0/0/12/24) |
| Evidence-span hit rate (12 spans) | 1.00 | 1.00 |
| Reproducibility max abs diff | 0.0 | 0.0 |

## Reading the numbers honestly

- The transformer preserves the human ordering and ranks the true match
  first ~82% of the time. Its F1 (0.57) reflects an uncalibrated default
  threshold, not a tuned classifier — thresholds remain defaults.
- The TF-IDF fallback **fails the ordering** (unrelated outscores related)
  and scores everything below the 0.50 cut, so F1 is 0.0. It measures word
  overlap, not meaning. It stays as a low-RAM fallback with an explicit
  `semantic_engine_warning` in every report it produces — never labelled
  equivalent to transformer similarity.
- This set is a regression/comparison signal (n=36, hand-written), **not** a
  validated benchmark. Growing it with adjudicated labels and re-running
  both engines is the top evaluation TODO.
