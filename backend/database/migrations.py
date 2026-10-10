"""Versioned schema migrations for the persistent analysis store.

Revisions are small, ordered, idempotent functions. `upgrade()` records each
applied revision in `schema_version` so restarts and redeploys converge.

Why not Alembic/Flask-Migrate here: the app has no app-factory and no
Flask-SQLAlchemy, so Alembic's autogenerate adds more machinery than value
for a two-table schema. If the schema grows beyond a handful of tables,
adopting Alembic is the documented next step (see docs/database.md).
"""
from database.models import SCHEMA_VERSION, Base, SchemaVersion

MIGRATIONS = []


def migration(version):
    def deco(fn):
        MIGRATIONS.append((version, fn))
        return fn
    return deco


@migration(1)
def _v1_initial_schema(engine):
    """Create analyses, review_events and schema_version tables."""
    Base.metadata.create_all(engine)


def current_version(engine):
    import sqlalchemy as _sa
    with engine.connect() as conn:
        if not engine.dialect.has_table(conn, "schema_version"):
            return 0
        try:
            row = conn.execute(_sa.text("SELECT MAX(version) FROM schema_version")).first()
            return int(row[0]) if row and row[0] is not None else 0
        except Exception:
            return 0


def upgrade(engine):
    """Apply pending revisions in order. Returns the resulting version."""
    import sqlalchemy as _sa
    applied = []
    for version, fn in sorted(MIGRATIONS):
        if version > current_version(engine):
            fn(engine)
            from datetime import datetime, timezone
            with engine.begin() as conn:
                conn.execute(_sa.text(
                    "INSERT INTO schema_version (version, applied_at) "
                    "VALUES (:v, :t)").bindparams(
                        v=version,
                        t=datetime.now(timezone.utc).isoformat()))
            applied.append(version)
    return current_version(engine), applied


def upgrade_database(database_url):
    """Entry point used at startup and by manage_db.py. Raises on failure."""
    import os as _os
    from database.models import get_engine
    if database_url.startswith("sqlite:"):
        # SQLite will not create missing parent directories itself.
        raw = database_url.split("sqlite:", 1)[1].lstrip("/")
        if raw and raw != ":memory:":
            # Handle Windows drive paths (C:/...) that lost a slash.
            if len(raw) > 2 and raw[1] == ":":
                fs_path = raw
            else:
                fs_path = (raw if _os.path.isabs("/" + raw)
                           else _os.path.abspath(raw))
                if fs_path.startswith("/") and len(fs_path) > 2 and fs_path[2] == ":":
                    fs_path = fs_path[1:]
            try:
                _os.makedirs(_os.path.dirname(fs_path) or ".", exist_ok=True)
            except Exception as e:
                raise RuntimeError(
                    "Cannot create database directory "
                    f"({type(e).__name__}). Check DATABASE_URL.") from e
    try:
        engine = get_engine(database_url)
    except Exception as e:
        raise RuntimeError(
            f"Cannot initialise database engine ({type(e).__name__}): "
            "check DATABASE_URL (sqlite:///... locally, postgresql://... in "
            "production). No credentials are shown here.") from e
    try:
        version, applied = upgrade(engine)
    except Exception as e:
        raise RuntimeError(
            f"Database migration failed at schema v{current_version(engine)} "
            f"({type(e).__name__}: {e}). Refusing to start with a half-migrated "
            "schema; restore from backup or run manage_db.py upgrade.") from e
    if version < SCHEMA_VERSION:
        raise RuntimeError(
            f"Schema is at v{version}, expected v{SCHEMA_VERSION}; upgrade() "
            "did not converge. Do not run with a stale schema.")
    return engine, version
