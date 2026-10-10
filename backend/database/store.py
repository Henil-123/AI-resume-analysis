"""Repository: the single persistence interface for v1 analyses.

Design rules:
- The database is authoritative. `_ANALYSES` in app.py is a read-through
  cache only; every read falls back to the store, every write goes to the
  store first. Two processes serving the same DATABASE_URL see the same
  record, and records survive restarts.
- Scoring updates + review-event inserts happen in ONE transaction, with an
  optimistic-locking `version` bump so concurrent reviews conflict (409)
  instead of silently overwriting each other.
- Review events are append-only: updates never erase the original result or
  earlier events.
- Raw resume text is never stored here; only file metadata, JD source text,
  scores, evidence summaries and the report.
- On any database error, raise AnalysisStoreError with an actionable message
  and no secrets. Production must NOT silently fall back to memory.
"""
import json
import uuid
from datetime import datetime, timezone

from database.models import (Analysis, ReviewEvent, get_engine,
                             session_factory_for)

VALID_REVIEW_ACTIONS = {"exclude", "restore", "reweight", "mandatory"}


class AnalysisStoreError(RuntimeError):
    pass


def _loads(text, default):
    try:
        return json.loads(text) if text else default
    except Exception:
        return default


def _dumps(obj):
    return json.dumps(obj, default=str)


class AnalysisStore:
    def __init__(self, database_url, engine=None):
        self.database_url = database_url
        try:
            self.engine = engine if engine is not None else get_engine(database_url)
            self.sessions = session_factory_for(database_url) if engine is None else \
                __import__("sqlalchemy.orm", fromlist=["sessionmaker"]).sessionmaker(
                    bind=engine, expire_on_commit=False)
        except Exception as e:
            raise AnalysisStoreError(
                "Cannot connect to the analysis database "
                f"({type(e).__name__}). Check DATABASE_URL and that the "
                "database server is reachable; refusing in-memory fallback.") from e

    # ── health ────────────────────────────────────────────────
    def ping(self):
        import sqlalchemy as _sa
        try:
            with self.engine.connect() as conn:
                conn.execute(_sa.text("SELECT 1"))
            return True
        except Exception as e:
            raise AnalysisStoreError(
                f"Analysis database unreachable ({type(e).__name__}).") from e

    # ── writes ────────────────────────────────────────────────
    def create_analysis(self, payload):
        """Persist a fresh analysis. Returns the collision-resistant id."""
        analysis_id = uuid.uuid4().hex
        agg = ((payload.get("explainable") or {}).get("aggregate")) or {}
        fi = agg.get("formula_inputs") or {}
        meta = _first_model_meta((payload.get("explainable") or {}).get("requirements") or [])
        now = datetime.now(timezone.utc)
        try:
            with self.sessions.begin() as s:
                s.add(Analysis(
                    id=analysis_id, created_at=now, updated_at=now, version=1,
                    resume_file=payload.get("resume_file", "") or "",
                    required_experience=int(payload.get("required_experience") or 0),
                    job_description=(payload.get("job_description") or "")[:10000],
                    jd_meta=_dumps(payload.get("jd_meta") or {}),
                    config_version=str(agg.get("config_version") or ""),
                    formula_version=str(agg.get("formula_version") or ""),
                    weights=_dumps(agg.get("weights") or {}),
                    engine=str((meta or {}).get("engine") or ""),
                    model=str((meta or {}).get("model") or ""),
                    model_version=str((meta or {}).get("version") or ""),
                    embedding_dim=(meta or {}).get("dimension"),
                    k_exact=repr(float(fi.get("K", 0.0))),
                    m_exact=repr(float(fi.get("M", 0.0))),
                    e_exact=repr(float(fi.get("E", 0.0))),
                    overall_exact=repr(float(fi.get("overall_exact", 0.0))),
                    overall_display=str(agg.get("overall_score")),
                    eligibility=_dumps((payload.get("explainable") or {}).get("eligibility") or {}),
                    requirements=_dumps((payload.get("explainable") or {}).get("requirements") or []),
                    aggregate=_dumps(agg),
                    evidence_summary=_dumps(
                        ((payload.get("explainable") or {}).get("report") or {}).get(
                            "verified_experience") or {}),
                    gaps=_dumps((payload.get("explainable") or {}).get("gaps") or []),
                    report=_dumps((payload.get("explainable") or {}).get("report") or {}),
                    candidate=_dumps(payload.get("candidate") or {}),
                ))
        except Exception as e:
            raise AnalysisStoreError(
                f"Could not persist analysis ({type(e).__name__}).") from e
        return analysis_id

    def get_analysis(self, analysis_id):
        """Return the stored payload dict, or None. Includes version/updated_at."""
        try:
            with self.sessions() as s:
                row = s.get(Analysis, analysis_id)
                if row is None or row.deleted:
                    return None
                return _row_to_payload(row)
        except Exception as e:
            raise AnalysisStoreError(
                f"Could not read analysis ({type(e).__name__}).") from e

    def update_scores(self, analysis_id, explainable, candidate_patch=None,
                      expected_version=None):
        """Replace scores/report atomically + bump version. Returns new version.

        Raises VersionConflict / AnalysisNotFound (both AnalysisStoreError
        subclasses) so the API can map them to 409/404.
        """
        agg = (explainable or {}).get("aggregate") or {}
        fi = agg.get("formula_inputs") or {}
        meta = _first_model_meta((explainable or {}).get("requirements") or [])
        try:
            with self.sessions.begin() as s:
                row = s.get(Analysis, analysis_id)
                if row is None or row.deleted:
                    raise AnalysisNotFound(analysis_id)
                if (expected_version is not None
                        and int(expected_version) != int(row.version)):
                    raise VersionConflict(analysis_id, int(expected_version),
                                          int(row.version))
                row.version = int(row.version) + 1
                row.updated_at = datetime.now(timezone.utc)
                row.config_version = str(agg.get("config_version") or "")
                row.formula_version = str(agg.get("formula_version") or "")
                row.weights = _dumps(agg.get("weights") or {})
                row.k_exact = repr(float(fi.get("K", 0.0)))
                row.m_exact = repr(float(fi.get("M", 0.0)))
                row.e_exact = repr(float(fi.get("E", 0.0)))
                row.overall_exact = repr(float(fi.get("overall_exact", 0.0)))
                row.overall_display = str(agg.get("overall_score"))
                row.eligibility = _dumps(explainable.get("eligibility") or {})
                row.requirements = _dumps(explainable.get("requirements") or [])
                row.aggregate = _dumps(agg)
                row.gaps = _dumps(explainable.get("gaps") or [])
                row.report = _dumps(explainable.get("report") or {})
                if candidate_patch is not None:
                    row.candidate = _dumps(candidate_patch)
                new_version = int(row.version)
        except (AnalysisNotFound, VersionConflict):
            raise
        except Exception as e:
            raise AnalysisStoreError(
                f"Could not update analysis scores ({type(e).__name__}).") from e
        return new_version

    def append_review_events(self, analysis_id, events):
        """Insert audit rows (append-only). Each event dict needs action etc."""
        try:
            with self.sessions.begin() as s:
                row = s.get(Analysis, analysis_id)
                if row is None or row.deleted:
                    raise AnalysisNotFound(analysis_id)
                for ev in events:
                    action = (ev.get("action") or "").strip()
                    if action not in VALID_REVIEW_ACTIONS:
                        raise AnalysisStoreError(
                            f"Invalid review action {action!r}; expected one of "
                            f"{sorted(VALID_REVIEW_ACTIONS)}.")
                    s.add(ReviewEvent(
                        id=uuid.uuid4().hex, analysis_id=analysis_id,
                        actor=str(ev.get("actor") or "")[:80],
                        action=action,
                        requirement_id=str(ev.get("requirement_id") or "")[:128],
                        previous_value=_dumps(ev.get("previous_value")),
                        updated_value=_dumps(ev.get("updated_value")),
                        reason=str(ev.get("reason") or "")[:300]))
        except (AnalysisNotFound, AnalysisStoreError):
            raise
        except Exception as e:
            raise AnalysisStoreError(
                f"Could not record review events ({type(e).__name__}).") from e

    def list_review_events(self, analysis_id):
        try:
            with self.sessions() as s:
                rows = (s.query(ReviewEvent)
                        .filter(ReviewEvent.analysis_id == analysis_id)
                        .order_by(ReviewEvent.created_at).all())
                return [{"id": r.id, "analysis_id": r.analysis_id,
                         "created_at": (r.created_at.isoformat()
                                        if r.created_at else ""),
                         "actor": r.actor, "action": r.action,
                         "requirement_id": r.requirement_id,
                         "previous_value": _loads(r.previous_value, None),
                         "updated_value": _loads(r.updated_value, None),
                         "reason": r.reason} for r in rows]
        except Exception as e:
            raise AnalysisStoreError(
                f"Could not list review events ({type(e).__name__}).") from e

    def delete_analysis(self, analysis_id):
        """Soft delete (retention policy) + return the resume filename."""
        try:
            with self.sessions.begin() as s:
                row = s.get(Analysis, analysis_id)
                if row is None or row.deleted:
                    return None
                row.deleted = 1
                row.updated_at = datetime.now(timezone.utc)
                return row.resume_file
        except Exception as e:
            raise AnalysisStoreError(
                f"Could not delete analysis ({type(e).__name__}).") from e


class AnalysisNotFound(AnalysisStoreError):
    def __init__(self, analysis_id):
        super().__init__(f"Analysis {analysis_id} not found.")
        self.analysis_id = analysis_id


class VersionConflict(AnalysisStoreError):
    def __init__(self, analysis_id, expected, current):
        super().__init__(
            f"Analysis {analysis_id} changed since you read it "
            f"(expected version {expected}, current {current}). Re-read and retry.")
        self.analysis_id = analysis_id
        self.expected = expected
        self.current = current


def _first_model_meta(requirements):
    for r in requirements or []:
        meta = r.get("semantic_model_meta")
        if meta and meta.get("model"):
            return meta
    return {}


def _row_to_payload(row):
    agg_reqs = _loads(row.requirements, [])
    report = _loads(row.report, {})
    eligibility = _loads(row.eligibility, {})
    candidate = _loads(row.candidate, {})
    agg = _loads(row.aggregate, {}) or {
        "status": "ok",
        "overall_score": float(row.overall_display or 0),
        "formula_inputs": {
            "K": float(row.k_exact or 0), "M": float(row.m_exact or 0),
            "E": float(row.e_exact or 0),
            "overall_exact": float(row.overall_exact or 0),
        },
        "config_version": row.config_version or "",
        "formula_version": row.formula_version or "",
        "weights": _loads(row.weights, {}),
    }
    return {
        "id": row.id,
        "version": int(row.version),
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "resume_file": row.resume_file or "",
        "required_experience": int(row.required_experience or 0),
        "job_description": row.job_description or "",
        "jd_meta": _loads(row.jd_meta, {}),
        "candidate": candidate,
        "explainable": {
            "status": "ok",
            "requirements": agg_reqs,
            "requirement_scores": agg_reqs,
            "aggregate": agg,
            "eligibility": eligibility,
            "gaps": _loads(row.gaps, []),
            "report": report,
            "config_version": row.config_version or "",
            "formula_version": row.formula_version or "",
        },
    }
