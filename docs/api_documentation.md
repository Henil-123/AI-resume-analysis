# API Documentation

Base: `http://localhost:5000` (local) or Render URL (hosted).

## Legacy (unchanged contracts)
| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | status + `spacy_model`, `embed_model`, `config_version`, `formula_version` |
| POST | `/analyze` | single resume (+ `explainable` block when JD present) |
| POST | `/analyze-batch` | batch (+ per-candidate `explainable`) |
| GET/DELETE | `/candidates`, `/candidates/<id>` | list/get/delete |
| PATCH | `/candidates/<id>/status` | shortlist pipeline |
| POST | `/compare` | side-by-side |
| POST | `/parse-jd`, `/extract-jd-skills` | JD understanding |
| GET | `/stats` | dashboard |
| DELETE | `/clear` | dev only (403 in production) |

## Versioned explainable API (`/api/v1`)
| Method | Endpoint | Body | Returns |
|---|---|---|---|
| GET | `/api/v1/health` | — | health + `api_version`, `embedding_dimension` |
| POST | `/api/v1/documents/resume` | `resume` file | extraction preview, sections, skills, status |
| POST | `/api/v1/documents/jd` | `jd_file`/`job_description` | JD understanding + structured `requirements` |
| POST | `/api/v1/analyses` | `resume`, `job_description`/`jd_file`, `required_skills?`, `required_experience?`, `weights?` (JSON string) | 201 `{analysis_id, candidate, report}` |
| GET | `/api/v1/analyses/<id>` | — | candidate + explainable + JD meta (DB fallback) |
| GET | `/api/v1/analyses/<id>/requirements` | — | requirement rows + aggregate |
| GET | `/api/v1/analyses/<id>/report` | `?format=json` downloads | 8-section report |
| POST | `/api/v1/analyses/<id>/recalculate` | `{weights:{keyword,semantic,experience}}` sum=1.0 | re-scored aggregate + requirements |

Errors: `{error}` with 400 (validation), 400/404/410/422/500 semantics.
Scores are never returned for failed required stages.

### Example

```bash
curl -X POST http://localhost:5000/api/v1/analyses \
  -F "resume=@candidate.txt" \
  -F "job_description=Senior Python Developer. Must have: Python, PostgreSQL." \
  -F "required_experience=3"
# -> 201 {analysis_id, candidate: {final_score, explainable: {aggregate, requirement_scores, eligibility, gaps}}, report}
```
