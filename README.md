# 🤖 AI Resume Analyzer

> Cloud-ready NLP system that automatically parses resumes, extracts skills, and ranks candidates against job requirements.

![Python](https://img.shields.io/badge/Python-3.11-blue)
![Flask](https://img.shields.io/badge/Flask-3.0-green)
![spaCy](https://img.shields.io/badge/spaCy-3.7-orange)
![License](https://img.shields.io/badge/License-MIT-purple)

---

## ✨ Features

- **PDF / DOCX / TXT** resume parsing
- **NLP skill extraction** using spaCy PhraseMatcher (200+ skills)
- **Semantic matching** using Sentence-BERT
- **Candidate scoring** with keyword + semantic + experience signals
- **Dashboard** with score distribution and top skills analytics
- **Candidate management** — view, rank, delete
- **MongoDB** support with local JSON fallback (no setup needed)
- **Cloud ready** — Docker + AWS EC2 + S3 support

---

## 🚀 Quick Start (3 commands)

```bash
# 1. Clone
git clone https://github.com/YOUR_USERNAME/ai-resume-analyzer.git
cd ai-resume-analyzer

# 2. Setup (installs everything automatically)
bash setup.sh

# 3. Run
cd backend && source venv/bin/activate && python app.py
```

Then open `frontend/index.html` in your browser.

---

## 🏗 Project Structure

```
ai-resume-analyzer/
├── backend/
│   ├── app.py                    # Flask API server
│   ├── services/
│   │   ├── resume_parser.py      # PDF/DOCX text extraction
│   │   ├── skill_extractor.py    # NLP skill detection (spaCy)
│   │   └── job_matcher.py        # Scoring engine + BERT
│   ├── database/
│   │   └── db_handler.py         # MongoDB + local JSON fallback
│   └── requirements.txt
├── frontend/
│   └── index.html                # Complete UI (no build step)
├── data/
│   └── skills/skills_dataset.json  # 200+ skills database
├── deployment/
│   └── Dockerfile
└── setup.sh                      # One-command setup
```

---

## 🧠 Tech Stack

| Layer | Technology |
|-------|-----------|
| NLP | spaCy, NLTK, Sentence-Transformers (BERT) |
| Backend | Python 3.11, Flask, Flask-CORS |
| Database | MongoDB Atlas (optional) / Local JSON |
| Storage | Local / AWS S3 (optional) |
| Frontend | Vanilla JS, Chart.js |
| Deployment | Docker, AWS EC2, Vercel |

---

## 📡 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | API status check |
| POST | `/analyze` | Upload and analyze resume |
| GET | `/candidates` | Get all candidates (sorted by score) |
| GET | `/candidates/:id` | Get single candidate |
| DELETE | `/candidates/:id` | Delete a candidate |
| GET | `/stats` | Dashboard statistics |
| DELETE | `/clear` | Clear all candidates |

---

## ⚙️ Configuration

Edit `backend/.env`:

```env
PORT=5000
MONGO_URI=            # Leave empty to use local JSON storage
DB_NAME=resume_analyzer

# Optional AWS S3
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_BUCKET_NAME=
```

---

## 🐳 Docker Deployment

```bash
docker build -f deployment/Dockerfile -t resume-analyzer .
docker run -p 5000:5000 resume-analyzer
```

On low-RAM hosts (Render free tier) the image defaults to
`SEMANTIC_ENGINE=tfidf` — a dependency-free cosine engine that avoids
loading torch into memory. Local Docker runs can opt into full BERT
accuracy with `-e SEMANTIC_ENGINE=bert`.

---

## 🚀 Hosted Deployment (Render + Vercel)

One backend on Render, one frontend on Vercel — see `DEPLOY.md` for the
full guide, and `docs/deployment_verification.md` for the 13-step live
checklist (do not claim success until it is run against the live URLs).
Production requires `DATABASE_URL` (PostgreSQL), `CORS_ORIGINS`, and
`REVIEW_API_TOKEN`; SQLite is single-instance only. `GET /ready` reports
config + store readiness without triggering model downloads.

---

## 📈 How Scoring Works (explainable-v2)

Primary overall score: **S = 0.60·K + 0.30·M + 0.10·E**
(keyword coverage · semantic relevance · experience fit), computed over
per-requirement scores **Rᵢ = 100·(0.35·Kᵢ + 0.35·Mᵢ + 0.30·Pᵢ)**
(keyword · semantic · evidence strength). Every requirement shows its
evidence quote, section/page source, raw cosine + normalized score, and
status (matched / partial / missing / uncertain). Eligibility
(eligible / ineligible / unknown) is evaluated separately — unknown stays
unknown, never a silent fail. Full methodology: `docs/scoring_methodology.md`;
model card: `docs/model_card.md`; API: `docs/api_documentation.md`.

Legacy precision signals (45/30/15/10 + must-have gate) still power the
headline `final_score` so existing dashboards keep working; the explainable
`S` score and requirement table ride along in every `/analyze` response.

## 🧪 Testing

```bash
cd backend && ./venv/Scripts/python -m pytest tests -q -p no:randomly
# Full suite (scoring, evidence, persistence, review security, upload
# security, PDF/JSON consistency, semantic eval, end-to-end)
```

```bash
cd backend && python -m eval.evaluate --engine tfidf   # or --engine bert
# Versioned semantic evaluation (docs/evaluation.md holds measured results)
```

```bash
cd backend && python manage_db.py upgrade   # apply schema migrations
```

## 🗄 Persistent storage

v1 analyses live in a relational store — SQLite file locally
(`data/analyses.db`), PostgreSQL via `DATABASE_URL` in production — with an
append-only review audit trail. Records survive restarts and are shared
between instances on the same database. See `docs/database.md` (schema,
migrations, retention) and `backend/.env.example` for all variables.

## 📡 API (v1 explainable)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/analyses` | Resume+JD comparison with full report |
| GET | `/api/v1/analyses/:id` | Retrieve report |
| GET | `/api/v1/analyses/:id/requirements` | Requirement-level matches |
| PATCH | `/api/v1/analyses/:id/requirements` | Manual review (bearer token); re-scores |
| GET | `/api/v1/analyses/:id/reviews` | Append-only review audit trail |
| GET | `/api/v1/analyses/:id/report` | Download report JSON (`?format=json`) or PDF (`?format=pdf`) |
| POST | `/api/v1/analyses/:id/recalculate` | Re-run with different weights |
| DELETE | `/api/v1/analyses/:id` | Delete analysis + resume file (bearer token) |
| GET | `/api/v1/health` | Health + model readiness |
| GET | `/ready` | Readiness: config + store checks |

Review example:

```bash
curl -X PATCH $API/api/v1/analyses/<id>/requirements \
  -H "Authorization: Bearer $REVIEW_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"exclude": ["asp.net"], "weights": {"java": 5},
       "expected_version": 1, "reason": "no .NET curriculum for this role"}'
```

---

## 📈 How Scoring Works (precision model)

| Signal | Weight | Description |
|--------|--------|-------------|
| Weighted skill coverage | 45% | JD section weights (must-have ×2, nice-to-have ×0.7, frequency emphasis) × applicant-pool IDF rarity, alias-aware + fuzzy typo recovery |
| Semantic fit | 30% | BGE chunked MaxSim (each JD chunk takes its best resume chunk) + global cosine; model cached, JD embedded once per batch |
| Experience | 15% | Years vs required, with date-range inference |
| Education | 10% | Degree-level fit |

Must-have gate: −6 pts per missing core skill (capped at −24), so resumes lacking core requirements can't top the ranking on nice-to-haves alone. If no JD/model is available, the semantic weight redistributes to skills.

Empty or unreadable resume files are rejected with a per-file error instead of scoring 0 as "Unknown Candidate". Typos missed by skill extraction (e.g. `pythn`) are recovered by scanning raw resume text at partial credit.

Score labels: **Strong Match** (80%+) · **Good Match** (60-79%) · **Partial** (40-59%) · **Weak** (<40%)

---

## ⚠️ Known limitations

- Semantic thresholds are defaults, not calibrated (eval: `docs/evaluation.md`).
  The TF-IDF fallback fails human band ordering — every report it produces
  says so via `semantic_engine_warning`.
- The review token is shared/internal; per-user ownership is future work.
- Rate limiting is per-process; add edge limiting in production.
- Live Render/Vercel verification is a manual checklist
  (`docs/deployment_verification.md`), not yet executed from CI here.
- Remaining risks: `docs/security.md`.

## 🗺 Roadmap

- [ ] Multi-resume bulk upload
- [ ] ATS score improvement suggestions
- [ ] AI chatbot for HR queries
- [ ] Resume improvement recommendations
- [ ] Export candidates to CSV/Excel

---

## 👨‍💻 Author

Built as an internship portfolio project demonstrating AI, NLP, Cloud architecture, and full-stack engineering.
