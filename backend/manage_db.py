"""Database management CLI: migrate, inspect, prune expired records.

Usage (from backend/):
    python manage_db.py upgrade            # apply pending schema revisions
    python manage_db.py version            # show current schema version
    python manage_db.py prune [--dry-run]  # soft-delete records older than RETENTION_DAYS
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv

load_dotenv()


def main(argv):
    import app_config
    from database.migrations import current_version, upgrade_database
    from database.models import get_engine

    cmd = argv[1] if len(argv) > 1 else "upgrade"
    if cmd == "version":
        print("schema version:", current_version(get_engine(app_config.DATABASE_URL)))
    elif cmd == "upgrade":
        _, version = upgrade_database(app_config.DATABASE_URL)
        print("schema version:", version)
    elif cmd == "prune":
        dry = "--dry-run" in argv
        from datetime import datetime, timedelta, timezone
        from database.store import AnalysisStore
        cutoff = datetime.now(timezone.utc) - timedelta(days=app_config.RETENTION_DAYS)
        store = AnalysisStore(app_config.DATABASE_URL)
        import sqlalchemy as _sa
        with store.sessions() as s:
            from database.models import Analysis
            rows = s.query(Analysis).filter(Analysis.deleted == 0,
                                            Analysis.created_at < cutoff).all()
            print(f"{len(rows)} record(s) older than {app_config.RETENTION_DAYS}d "
                  f"(cutoff {cutoff.date()})")
            if not dry:
                for r in rows:
                    r.deleted = 1
                s.commit()
                print(f"soft-deleted {len(rows)} record(s). Resume files must be "
                      f"removed via DELETE /api/v1/analyses/<id> or manual cleanup.")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
