# 🤖 AI Resume Analyzer

> Cloud-ready NLP system that automatically parses resumes, extracts skills, and ranks candidates against job requirements.

![Python](https://img.shields.io/badge/Python-3.11-blue)
![Flask](https://img.shields.io/badge/Flask-3.0-green)
![spaCy](https://img.shields.io/badge/spaCy-3.7-orange)
![Groq](https://img.shields.io/badge/Groq-Llama_3.1-orange)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## ✨ Features

- **Multi-format resume parsing** — PDF, DOCX, TXT via PyPDF2/pdfminer + python-docx
- **NLP skill extraction** — spaCy PhraseMatcher with 500+ skills across 16 categories
- **86 skill aliases** — ML → Machine Learning, JS → JavaScript, K8s → Kubernetes, etc.
- **Semantic matching** — Groq AI (Llama 3.1) for high-speed resume-JD similarity
- **3-signal scoring** — Keyword match + Semantic match + Experience, with configurable weights
- **3 weight profiles** — Technical (skill-focused), Manager (experience-focused), Entry (breadth-focused)
- **Multi-resume bulk upload** — Sequential batch analysis with ranked results
- **Candidate management** — View, filter, sort, compare, status updates, bulk actions
- **AI explanations** — Auto-generated strengths, weaknesses, warnings, and verdict
- **Dashboard analytics** — Score distribution, top skills, education breakdown, top candidates
- **CSV export** — Download filtered candidate data with all fields
- **Printable reports** — Browser-printable ranked candidate summary
- **Duplicate detection** — SHA-256 hash prevents re-analyzing the same resume
- **JWT authentication** — Signup/login with token-based auth
- **MongoDB + JSON fallback** — Works with MongoDB Atlas or zero-config local JSON storage
- **Docker + Cloud ready** — Dockerfile, Render.yaml, Vercel config included

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
│   ├── app.py                        # Flask API server (14 endpoints)
│   ├── requirements.txt
│   ├── .env.example                  # Environment template
│   ├── services/
│   │   ├── resume_parser.py          # PDF/DOCX/TXT text extraction + NER
│   │   ├── skill_extractor.py        # spaCy PhraseMatcher skill detection
│   │   ├── job_matcher.py            # BERT scoring engine + weight profiles
│   │   ├── explainer.py              # AI explanation generator
│   │   └── export.py                 # CSV export service
│   ├── database/
│   │   └── db_handler.py             # MongoDB + local JSON fallback
│   └── utils/
│       ├── auth.py                   # JWT authentication
│       └── logger.py                 # Structured logging
├── frontend/
│   ├── index.html                    # Complete SPA (no build step)
│   └── vercel.json                   # Vercel deployment config
├── data/
│   ├── skills/
│   │   ├── skills_dataset.json       # 500+ skills, 16 categories
│   │   └── skill_aliases.json        # 86 abbreviation mappings
│   ├── weight_profiles.json          # 3 scoring weight profiles
│   └── resumes/                      # Upload directory
├── deployment/
│   └── Dockerfile                    # Production Docker image
├── render.yaml                       # Render.com deployment config
├── setup.sh                          # One-command setup script
└── .gitignore
```

---

## 🧠 Tech Stack

| Layer | Technology |
|-------|-----------|
| NLP | spaCy 3.7 (PhraseMatcher + NER), Groq AI (Llama 3.1) |
| Backend | Python 3.11, Flask 3.0, Flask-CORS, Gunicorn |
| Database | MongoDB Atlas (optional) / Local JSON (zero config) |
| Frontend | Vanilla JS, Chart.js 4.4 |
| Deployment | Docker, Render (backend), Vercel (frontend) |

---

## 📡 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | API status check |
| `POST` | `/analyze` | Upload and analyze a single resume |
| `POST` | `/analyze/bulk` | Batch analyze multiple resumes |
| `GET` | `/candidates` | List all candidates (sorted, filtered) |
| `GET` | `/candidates/:id` | Get single candidate details |
| `PATCH` | `/candidates/:id/status` | Update status (shortlisted/rejected/maybe) |
| `DELETE` | `/candidates/:id` | Delete a candidate |
| `POST` | `/compare` | Side-by-side candidate comparison |
| `GET` | `/stats` | Dashboard statistics |
| `GET` | `/profiles` | Get weight profiles |
| `GET` | `/export/csv` | Download candidates as CSV |
| `DELETE` | `/clear` | Clear all candidates (dev only) |
| `POST` | `/auth/signup` | Register new user |
| `POST` | `/auth/login` | Authenticate user |

---

## ⚙️ Configuration

Edit `backend/.env`:

```env
PORT=5000
MONGO_URI=            # Leave empty to use local JSON storage
DB_NAME=resume_analyzer
JWT_SECRET=your-random-secret-here

# Optional: AWS S3 for resume file storage
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_BUCKET_NAME=
AWS_REGION=us-east-1
```

---

## 🐳 Docker Deployment

```bash
docker build -f deployment/Dockerfile -t resume-analyzer .
docker run -p 5000:5000 -e JWT_SECRET=your-secret resume-analyzer
```

---

## ☁️ Cloud Deployment

### Backend (Render)
1. Push code to GitHub
2. Connect to [Render.com](https://render.com)
3. Create new Web Service from `render.yaml`
4. Set `MONGO_URI` environment variable (optional)

### Frontend (Vercel)
1. Import `frontend/` folder to [Vercel](https://vercel.com)
2. `vercel.json` handles API proxy rewrites automatically

---

## 📈 How Scoring Works

| Signal | Default Weight | Description |
|--------|---------------|-------------|
| Keyword match | 60% | Skills found vs required skills (set intersection) |
| Semantic match | 30% | BERT cosine similarity of resume vs job description |
| Experience | 10% | Years of experience vs required years |

### Weight Profiles

| Profile | Skills | Semantic | Experience | Best for |
|---------|--------|----------|------------|----------|
| Technical | 60% | 30% | 10% | Engineering, developer roles |
| Manager | 30% | 50% | 20% | Leadership, management roles |
| Entry | 70% | 20% | 10% | Junior, intern roles |

### Score Labels
**Strong Match** (80%+) · **Good Match** (60-79%) · **Partial Match** (40-59%) · **Weak Match** (<40%)

---

## 🗺 Roadmap

- [x] Multi-resume bulk upload with ranked results
- [x] Export candidates to CSV
- [x] Weight profiles (Technical/Manager/Entry)
- [x] Candidate comparison
- [x] AI-generated explanations
- [x] Print-ready reports
- [ ] ATS score improvement suggestions
- [ ] AI chatbot for HR queries
- [ ] Resume improvement recommendations
- [ ] Email notifications for shortlisted candidates

---

## 👨‍💻 Author

Built as an internship portfolio project demonstrating AI, NLP, Cloud architecture, and full-stack engineering.
