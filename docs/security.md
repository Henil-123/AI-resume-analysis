# Security

## Implemented this phase

- **Upload validation**: extension allowlist + magic-byte sniffing (PDF
  `%PDF`, DOCX `PK`), MIME sanity, per-file size cap, `MAX_CONTENT_LENGTH`
  → clean 413 (previously masked as 500), encrypted PDFs rejected with
  guidance, textless PDFs get an OCR hint instead of a zero score.
- **Filenames**: `secure_filename` + generated `uuid` names; traversal names
  neutralized (tested). Temp JD files removed after parsing.
- **Manual review** (`PATCH .../requirements`): strict schema allowlist,
  finite weights in [1, 5], boolean mandatory flags, exclude+restore clash
  rejected, `expected_version` optimistic locking → 409, single transaction
  for scores + audit, bearer token when `REVIEW_API_TOKEN` is set, review
  events logged without resume text or secrets, 30/min rate limit.
- **Destructive**: `DELETE .../analyses/<id>` requires the same token and
  removes record + resume file. `/clear` stays disabled in production.
- **CORS**: explicit `CORS_ORIGINS`/`FRONTEND_ORIGIN`; permissive only in dev
  (production startup refuses to run without origins).
- **Errors**: generic 500s in production (details server-side only);
  404s carry no leaks; IDs validated (`[A-Za-z0-9_-]{8,64}`).
- **Config**: `backend/app_config.py` validates at startup, logs a
  redacted summary, never prints secrets.

## Remaining risks (not hidden)

1. **No per-user ownership.** The review token is a shared internal secret;
   anyone with it can modify any analysis. Per-user auth + ownership is the
   next security milestone. Until then: keep the token server-side, rotate
   on staff changes, never ship it in the frontend.
2. **Rate limiting is per-process memory.** Under multiple workers it is
   approximate. Add edge (Render/NGINX) rate limiting for real protection.
3. **Heuristic parsers.** PDF/DOCX parsing of hostile files is best-effort;
   size caps + header checks bound the damage but malformed-file fuzzing is
   still TODO.
4. **PII in the database.** Analyses contain names/emails by design. Restrict
   DB access, encrypt backups, honour deletion requests (see `docs/database.md`).
5. Passing tests prove the listed behaviours, not absence of vulnerabilities.
