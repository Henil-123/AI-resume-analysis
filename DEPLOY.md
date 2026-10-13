# Deploy guide — one Render backend + one Vercel frontend

## Architecture

- **Backend** (Flask): deployed on **Render** from `deployment/Dockerfile`.
  Serves the API **and** the frontend statically (so Render alone is usable).
- **Frontend** (static): deployed on **Vercel** from `frontend/`.
  API calls go to same-origin `/api/*`, rewritten to the Render backend.

## 1. Render (backend)

1. New → Web Service → connect `Henil-123/AI-resume-analysis`.
2. Settings:
   - Runtime: **Docker**, Dockerfile path: `deployment/Dockerfile`
   - Plan: Free; Health check path: `/health`
3. Environment variables (see `render.yaml`):
   - `APP_ENV=production`, `PORT=5000`
   - `SEMANTIC_ENGINE=tfidf` — **required on free tier**.
     The image no longer ships torch at all (`requirements.txt` is
     torch-free; ML deps are in `requirements-ml.txt` for local use), so
     `tfidf` is the only engine available in production. It is also the
     only one that fits 512 MB. Rankings come from weighted skills + IDF +
     evidence, not from the transformer.
   - `MONGO_URI` (optional — app falls back to local JSON store),
     `DB_NAME=resume_analyzer`, `JWT_SECRET` (generate), `GROQ_API_KEY`
     (legacy, unused — Groq was retired).
4. Deploy, then verify:
   - `GET https://<your-service>.onrender.com/health` →
     `{"status":"ok", ...}`. Must answer in **well under 1s**: that path is
     polled by Render's deploy check and the Docker `HEALTHCHECK`, so it
     never imports spaCy or any model. `spacy_model` reads
     `not-loaded-yet` until the first analysis loads it.

## 2. Vercel (frontend) — keep exactly ONE project

1. Import the same repo. **Root Directory: `frontend`**.
   (A root `vercel.json` is also provided if you prefer repo-root.)
2. The app calls the Render backend **directly from the browser**
   (`DEFAULT_BACKEND` in `frontend/app.js`, must match the `destination` in
   both `vercel.json` files). The `/api` rewrite stays only as a fallback
   (set `localStorage.backend_url='/api'` to use it) — it 502s long
   analysis POSTs through the proxy timeout, so direct is the default.
3. CORS: unset `CORS_ORIGINS` in production = allow-all with a startup
   warning (fine while the API carries no cookies; review/delete stay
   token-gated). Set `CORS_ORIGINS=https://<your-frontend>.vercel.app` on
   Render to lock down.
4. No rebuild needed for quick backend moves: in the live site console run
   `localStorage.setItem('backend_url','https://<your-service>.onrender.com')`
   and reload — or set `window.__BACKEND_URL__` before `app.js` loads.

## 3. Common failures

| Symptom | Cause | Fix |
|---|---|---|
| `OSError: [E050] en_core_web_sm` | model missing | Fixed in code (resilient loader + pinned wheel in `requirements.txt`). Rebuild. |
| `502` after ~60 s on first analysis | BERT cold-load / OOM on free tier | Already fixed: torch is out of the image, `SEMANTIC_ENGINE=tfidf` is in `render.yaml` + `Dockerfile`. |
| `WORKER TIMEOUT` then 502 for *everything* | A scoring request exceeded the gunicorn budget and killed the only worker; `/health` queued behind it | Already fixed: the ~80 s `import sentence_transformers` inside scoring is gone (`_sentence_transformers_version()` reads dist metadata instead), and 1 worker × 2 threads keeps `/health` answering. |
| `429` bursts on free Render | QA hammered the free instance | Wait 60 s, retry serially. Not an app bug. |
| Red X on GitHub commit | a connected Vercel project failing | Keep ONE Vercel project; delete duplicates or set its Root Directory to `frontend`. Code is unaffected. |
| Vercel login wall on the URL | Deployment Protection on | Vercel → Project → Settings → Deployment Protection → off. |
