"""Persistent analysis storage (SQLAlchemy ORM).

Authoritative store for v1 analyses and the append-only manual-review audit
trail. SQLite for local development, PostgreSQL for deployment via
DATABASE_URL. Payload columns use JSON-as-TEXT so the same schema works on
both backends without driver-specific types.

Schema versions are recorded in `schema_version` and advanced by
`database/migrations.py`. No silent in-memory fallback: if the database is
unreachable, every repository call raises with an actionable message and no
secrets.
"""
from datetime import datetime, timezone

from sqlalchemy import (Column, DateTime, ForeignKey, Integer, String, Text,
                        create_engine)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

SCHEMA_VERSION = 1


class Base(DeclarativeBase):
    pass


def _utcnow():
    return datetime.now(timezone.utc)


class Analysis(Base):
    """One authoritative analysis record. Scores are stored, never recomputed."""
    __tablename__ = "analyses"

    # Collision-resistant id (uuid4 hex, 32 chars). Never truncated.
    id = Column(String(64), primary_key=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow,
                        onupdate=_utcnow, nullable=False)
    # Optimistic-locking counter for concurrent manual reviews.
    version = Column(Integer, default=1, nullable=False)

    resume_file = Column(String(512), default="")
    required_experience = Column(Integer, default=0)
    # JD source text (truncated at write time) + parsed meta, no raw resume.
    job_description = Column(Text, default="")
    jd_meta = Column(Text, default="{}")

    config_version = Column(String(32), default="")
    formula_version = Column(String(32), default="")
    weights = Column(Text, default="{}")
    engine = Column(String(64), default="")
    model = Column(String(128), default="")
    model_version = Column(String(64), default="")
    embedding_dim = Column(Integer, nullable=True)

    # Full-precision components; display rounding never feeds back here.
    k_exact = Column(Text, default="0")
    m_exact = Column(Text, default="0")
    e_exact = Column(Text, default="0")
    overall_exact = Column(Text, default="0")
    overall_display = Column(Text, default="0")

    eligibility = Column(Text, default="{}")
    requirements = Column(Text, default="[]")
    # Full aggregate JSON (display values, formula, rounding, experience
    # detail). The scalar *_exact columns above stay for queryability; this
    # column is what the API serves so stored reads are byte-identical to
    # what was written.
    aggregate = Column(Text, default="{}")
    evidence_summary = Column(Text, default="[]")
    gaps = Column(Text, default="[]")
    report = Column(Text, default="{}")
    candidate = Column(Text, default="{}")

    # Soft delete for documented retention behaviour.
    deleted = Column(Integer, default=0, nullable=False)

    reviews = relationship("ReviewEvent", back_populates="analysis",
                           cascade="all, delete-orphan",
                           order_by="ReviewEvent.created_at")


class ReviewEvent(Base):
    """Append-only audit trail. Rows are inserted, never updated or deleted."""
    __tablename__ = "review_events"

    id = Column(String(64), primary_key=True)
    analysis_id = Column(String(64), ForeignKey("analyses.id"), nullable=False,
                         index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    actor = Column(String(80), default="")
    action = Column(String(32), nullable=False)  # exclude/restore/reweight/mandatory
    requirement_id = Column(String(128), default="")
    previous_value = Column(Text, default="")
    updated_value = Column(Text, default="")
    reason = Column(String(300), default="")

    analysis = relationship("Analysis", back_populates="reviews")


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    version = Column(Integer, primary_key=True)
    applied_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


def get_engine(database_url):
    if database_url.startswith("sqlite:"):
        return create_engine(database_url, connect_args={"check_same_thread": False})
    # PostgreSQL (psycopg v3 or psycopg2); pool sized for small hosts.
    return create_engine(database_url, pool_size=5, max_overflow=5,
                         pool_pre_ping=True)


def session_factory_for(database_url):
    return sessionmaker(bind=get_engine(database_url), expire_on_commit=False)
