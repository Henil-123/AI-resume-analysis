"""Environment-separated application configuration.

Development and production share code but never share defaults for security
decisions: CORS is permissive only outside production, destructive endpoints
are disabled in production, and secrets always come from the environment.

Startup validation fails fast with actionable messages that never include
secret values.
"""
import os

APP_ENV = os.getenv("APP_ENV", "development").lower()
IS_PROD = APP_ENV == "production" or os.getenv("FLASK_ENV", "") == "production"

PORT = int(os.getenv("PORT", "5000"))

# ── Database ──────────────────────────────────────────────────
# SQLite file for local dev; PostgreSQL URL for deployment.
# Never hardcode credentials: production requires DATABASE_URL or MONGO_URI
# to be set explicitly (see validate()).
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_SQLITE = os.path.join(os.path.dirname(_BACKEND_DIR),
                               "data", "analyses.db")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_DEFAULT_SQLITE}")
MONGO_URI = os.getenv("MONGO_URI", "")
DB_NAME = os.getenv("DB_NAME", "resume_analyzer")

# ── Security ──────────────────────────────────────────────────
# Comma-separated allowed origins for CORS. Empty = permissive in dev only;
# production MUST set this (validated at startup).
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "").strip()
if FRONTEND_ORIGIN and FRONTEND_ORIGIN not in CORS_ORIGINS:
    CORS_ORIGINS.append(FRONTEND_ORIGIN)

# Shared bearer token guarding manual-review and destructive endpoints.
# Unset = dev/internal use only; production refuses to start without it
# unless REVIEW_OPEN is explicitly set (documented risk, see docs).
REVIEW_API_TOKEN = os.getenv("REVIEW_API_TOKEN", "")
REVIEW_OPEN = os.getenv("REVIEW_OPEN", "").lower() in ("1", "true", "yes")

# ── Uploads / retention ───────────────────────────────────────
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "10"))
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "90"))
SEMANTIC_ENGINE = os.getenv("SEMANTIC_ENGINE", "bert").lower()


def validate():
    """Fail fast on unsafe production configuration. Never logs secrets."""
    problems = []
    if IS_PROD:
        if not CORS_ORIGINS:
            problems.append(
                "CORS_ORIGINS (or FRONTEND_ORIGIN) must be set in production; "
                "permissive CORS is dev-only.")
        if not REVIEW_API_TOKEN and not REVIEW_OPEN:
            problems.append(
                "Set REVIEW_API_TOKEN in production to guard PATCH "
                "/api/v1/analyses/<id>/requirements, or set REVIEW_OPEN=1 to "
                "explicitly accept the risk (internal use only).")
        if DATABASE_URL.startswith("sqlite:") and not os.getenv("ALLOW_SQLITE_IN_PROD"):
            problems.append(
                "SQLite is not shared between deployed instances; set "
                "DATABASE_URL to PostgreSQL (or ALLOW_SQLITE_IN_PROD=1 for a "
                "single-instance trial).")
    if MAX_FILE_MB <= 0 or MAX_FILE_MB > 100:
        problems.append(f"MAX_FILE_MB out of sane range (got {MAX_FILE_MB}).")
    if RETENTION_DAYS < 0:
        problems.append(f"RETENTION_DAYS must be >= 0 (got {RETENTION_DAYS}).")
    if problems:
        raise RuntimeError("Invalid configuration:\n- " + "\n- ".join(problems))


def safe_summary():
    """Loggable config snapshot with secrets redacted."""
    return {
        "app_env": APP_ENV,
        "port": PORT,
        "database": _redact_url(DATABASE_URL),
        "mongo_configured": bool(MONGO_URI),
        "cors_origins": CORS_ORIGINS or "(permissive: dev only)",
        "review_token_set": bool(REVIEW_API_TOKEN),
        "review_open": REVIEW_OPEN,
        "max_file_mb": MAX_FILE_MB,
        "retention_days": RETENTION_DAYS,
        "semantic_engine": SEMANTIC_ENGINE,
    }


def _redact_url(url):
    try:
        from urllib.parse import urlsplit, urlunsplit
        p = urlsplit(url)
        if p.password:
            return urlunsplit((p.scheme, f"{p.username}:***@{p.hostname}"
                               + (f":{p.port}" if p.port else ""), p.path, "", ""))
        return url
    except Exception:
        return "(unparseable url)"
