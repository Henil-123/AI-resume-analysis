# Deployment verification checklist

Do not claim deployment success without completing these against the live URLs.

Backend: `https://<service>.onrender.com` · Frontend: Vercel URL.

1. **Deploy backend.** Push to `main`; Render builds `deployment/Dockerfile`.
2. **Environment.** Set `DATABASE_URL` (PostgreSQL), `CORS_ORIGINS` (Vercel URL),
   `REVIEW_API_TOKEN` (generated), `APP_ENV=production`, `SEMANTIC_ENGINE=tfidf`
   (free tier) or unset (transformer on larger hosts). Never commit real values.
3. **Migrate.** Startup runs migrations automatically; confirm logs show
   `analysis store ready (schema v1)`. Or run `python manage_db.py upgrade`.
4. **Health.** `GET /health` → `config_version 2.0.0`,
   `formula_version explainable-v2`. `GET /ready` → `{"ready": true, ...}`.
5. **Frontend.** Deploy Vercel; set API base URL to the backend; check
   `vercel.json` rewrite destination matches the live backend host.
6. **Upload.** Submit one resume + JD end to end (Analyze → Rankings →
   Dashboard → Shortlist → Compare → Export).
7. **Retrieve.** `GET /api/v1/analyses/<id>` returns the same headline as the UI.
8. **Review.** `PATCH .../requirements` with the bearer token
   (`{"exclude": ["asp.net"], "reason": "verify"}`); confirm rescore + audit
   via `GET .../reviews`.
9. **Rescore persisted.** `POST .../recalculate` with 0.6/0.3/0.1; re-GET and
   confirm the new score stuck.
10. **PDF.** `GET .../report?format=pdf` opens; headline matches the UI.
11. **Restart.** Redeploy/restart the backend; re-GET the analysis — it must
    still exist with identical scores (persistent store, not memory).
12. **Auth/errors.** PATCH without token → 401; unknown id → 404 (no leak);
    oversized file → 413 with a clear message.
13. **Second instance.** If the host runs multiple workers/instances, create on
    one and read from the other; both must agree (shared `DATABASE_URL`).
    Single-instance SQLite cannot satisfy this — use PostgreSQL.

Note (2026-10-10): steps 1–13 were verified **locally** (full suite green);
live Render/Vercel execution was not performed from here (no deploy access in
this session). Do not mark live verification complete until someone runs it.
