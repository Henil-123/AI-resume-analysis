"""
Database Handler — Supabase (PostgreSQL) with local JSON fallback.

Features:
- Supabase REST client support + automatic JSON fallback
- Candidate CRUD operations
- Status management (shortlisted / rejected / maybe / pending)
- Filtering by score, experience, skills, status
- Duplicate detection via text hash
- Dashboard statistics
"""

import json
import os
import uuid
import hashlib
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

if os.environ.get('VERCEL'):
    LOCAL_DB_PATH = os.path.join('/tmp', 'candidates.json')
else:
    LOCAL_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "candidates.json")

_supabase = None
_use_supabase = False

VALID_STATUSES = {"pending", "shortlisted", "rejected", "maybe"}


def _get_supabase():
    global _supabase, _use_supabase
    if _supabase:
        return _supabase
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            from supabase import create_client, Client
            _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
            _use_supabase = True
            logger.info("Connected to Supabase")
            return _supabase
        except Exception as e:
            logger.warning(f"Supabase unavailable ({e}), using local JSON storage")
    return None


# ── Local JSON helpers ───────────────────────────────────────────

def _load_local():
    if not os.path.exists(LOCAL_DB_PATH):
        return []
    with open(LOCAL_DB_PATH, "r") as f:
        return json.load(f)


def _save_local(records):
    os.makedirs(os.path.dirname(LOCAL_DB_PATH), exist_ok=True)
    with open(LOCAL_DB_PATH, "w") as f:
        json.dump(records, f, indent=2, default=str)


# ── Hashing ──────────────────────────────────────────────────────

def compute_text_hash(text):
    """Compute SHA-256 hash of cleaned resume text for duplicate detection."""
    if not text:
        return None
    return hashlib.sha256(text.strip().lower().encode()).hexdigest()


# ── Public API ───────────────────────────────────────────────────

def save_candidate(data: dict) -> tuple:
    """
    Save a candidate record. Returns the generated ID.
    Checks for duplicates via text hash.
    """
    candidate_id = str(uuid.uuid4())[:8]

    # Compute text hash for duplicate detection
    clean_text = data.get("clean_text", "") or data.get("raw_text", "")
    text_hash = compute_text_hash(clean_text)

    # Check for duplicate
    if text_hash:
        existing = find_by_hash(text_hash)
        if existing:
            return existing.get("id"), True  # Return (id, is_duplicate)

    record = {
        "id": candidate_id,
        "created_at": datetime.utcnow().isoformat(),
        "status": "pending",
        "text_hash": text_hash,
        **data
    }

    # Remove raw_text and clean_text from stored record (too large)
    record.pop("raw_text", None)
    record.pop("clean_text", None)

    sb = _get_supabase()
    if sb is not None:
        try:
            # Upsert handles inserting without failing if there are constraints, but here basic insert is fine
            sb.table('candidates').insert(record).execute()
        except Exception as e:
            logger.error(f"Failed to insert candidate to Supabase: {e}")
            # Fallback to local
            records = _load_local()
            records.append(record)
            _save_local(records)
    else:
        records = _load_local()
        records.append(record)
        _save_local(records)

    return candidate_id, False  # Return (id, is_duplicate)


def find_by_hash(text_hash):
    """Find an existing candidate by text hash (for duplicate detection)."""
    sb = _get_supabase()
    if sb is not None:
        try:
            res = sb.table('candidates').select('*').eq('text_hash', text_hash).limit(1).execute()
            if res.data:
                return res.data[0]
        except Exception as e:
            logger.error(f"Error querying by hash: {e}")
    else:
        for r in _load_local():
            if r.get("text_hash") == text_hash:
                return r
    return None


def get_all_candidates(filters=None):
    """
    Return all candidates, optionally filtered.

    Supported filters:
    - min_score: minimum final_score
    - max_score: maximum final_score
    - min_experience: minimum experience_years
    - skills: comma-separated list of required skills (must have all)
    - status: candidate status (pending/shortlisted/rejected/maybe)
    """
    sb = _get_supabase()
    if sb is not None:
        try:
            query = sb.table('candidates').select('*')
            
            if filters:
                if "min_score" in filters:
                    query = query.gte("final_score", float(filters["min_score"]))
                if "max_score" in filters:
                    query = query.lte("final_score", float(filters["max_score"]))
                if "min_experience" in filters:
                    query = query.gte("experience_years", int(filters["min_experience"]))
                if "status" in filters:
                    query = query.eq("status", filters["status"])
                # Supabase array contained by / overlapping requires special operators. 
                # For simplicity, we fetch them and filter matched_skills in python if needed, 
                # or use Postgres array operators if perfectly aligned. 
                # Let's filter skills in Python to be safe and compatible with local fallback.
            
            res = query.order("final_score", desc=True).execute()
            results = res.data
            
            # Post-filter for skills since arrays with JSON REST API can be tricky
            if filters and "skills" in filters:
                skill_list = [s.strip().lower() for s in filters["skills"].split(",")]
                results = [r for r in results 
                           if all(s in r.get("matched_skills", []) for s in skill_list)]
                
        except Exception as e:
            logger.error(f"Error fetching candidates: {e}")
            results = []
    else:
        results = _load_local()
        results = _apply_local_filters(results, filters)
        results.sort(key=lambda x: x.get("final_score", 0), reverse=True)

    # Add rank to each result
    for i, r in enumerate(results):
        r["rank"] = i + 1
        r["is_top"] = i < 3

    return results


def _apply_local_filters(records, filters):
    """Apply filters to local JSON records."""
    if not filters:
        return records

    filtered = records.copy()

    if "min_score" in filters:
        min_s = float(filters["min_score"])
        filtered = [r for r in filtered if r.get("final_score", 0) >= min_s]

    if "max_score" in filters:
        max_s = float(filters["max_score"])
        filtered = [r for r in filtered if r.get("final_score", 0) <= max_s]

    if "min_experience" in filters:
        min_exp = int(filters["min_experience"])
        filtered = [r for r in filtered if r.get("experience_years", 0) >= min_exp]

    if "status" in filters:
        status = filters["status"]
        filtered = [r for r in filtered if r.get("status", "pending") == status]

    if "skills" in filters:
        skill_list = [s.strip().lower() for s in filters["skills"].split(",")]
        filtered = [r for r in filtered
                     if all(s in r.get("matched_skills", []) for s in skill_list)]

    return filtered


def get_candidate_by_id(candidate_id: str):
    """Return a single candidate by ID."""
    sb = _get_supabase()
    if sb is not None:
        try:
            res = sb.table('candidates').select('*').eq('id', candidate_id).single().execute()
            return res.data
        except Exception as e:
            logger.error(f"Error fetching candidate {candidate_id}: {e}")
            return None
    else:
        for r in _load_local():
            if r.get("id") == candidate_id:
                return r
    return None


def update_candidate_status(candidate_id: str, status: str) -> bool:
    """
    Update a candidate's status.
    Valid statuses: pending, shortlisted, rejected, maybe
    """
    if status not in VALID_STATUSES:
        return False

    sb = _get_supabase()
    if sb is not None:
        try:
            res = sb.table('candidates').update({"status": status}).eq('id', candidate_id).execute()
            return len(res.data) > 0
        except Exception as e:
            logger.error(f"Error updating status: {e}")
            return False
    else:
        records = _load_local()
        for r in records:
            if r.get("id") == candidate_id:
                r["status"] = status
                _save_local(records)
                return True
    return False


def delete_candidate(candidate_id: str) -> bool:
    """Delete a candidate by ID."""
    sb = _get_supabase()
    if sb is not None:
        try:
            res = sb.table('candidates').delete().eq('id', candidate_id).execute()
            return len(res.data) > 0
        except Exception as e:
            logger.error(f"Error deleting: {e}")
            return False
    else:
        records = _load_local()
        new_records = [r for r in records if r.get("id") != candidate_id]
        if len(new_records) < len(records):
            _save_local(new_records)
            return True
    return False


def clear_all_candidates():
    """Delete all candidates (for testing)."""
    sb = _get_supabase()
    if sb is not None:
        try:
            # We can only delete rows in REST if we have a filter,
            # so we match id is not null safely.
            sb.table('candidates').delete().not_('id', 'is', 'null').execute()
        except Exception as e:
            logger.error(f"Error clearing: {e}")
    else:
        _save_local([])


def get_stats():
    """Return aggregate stats for the dashboard."""
    candidates = get_all_candidates()
    if not candidates:
        return {
            "total": 0, "avg_score": 0,
            "top_skills": [], "strong_matches": 0,
            "status_counts": {"pending": 0, "shortlisted": 0, "rejected": 0, "maybe": 0}
        }

    scores = [c.get("final_score", 0) for c in candidates]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0
    strong_matches = sum(1 for s in scores if s >= 80)

    # Count skill frequency
    skill_freq = {}
    for c in candidates:
        for skill in c.get("skills_found", []):
            skill_freq[skill] = skill_freq.get(skill, 0) + 1

    top_skills = sorted(skill_freq.items(), key=lambda x: x[1], reverse=True)[:10]
    top_skills = [{"skill": s, "count": c} for s, c in top_skills]

    # Status counts
    status_counts = {"pending": 0, "shortlisted": 0, "rejected": 0, "maybe": 0}
    for c in candidates:
        s = c.get("status", "pending")
        if s in status_counts:
            status_counts[s] += 1

    # Score distribution
    score_ranges = {"0-20": 0, "21-40": 0, "41-60": 0, "61-80": 0, "81-100": 0}
    for s in scores:
        if s <= 20:
            score_ranges["0-20"] += 1
        elif s <= 40:
            score_ranges["21-40"] += 1
        elif s <= 60:
            score_ranges["41-60"] += 1
        elif s <= 80:
            score_ranges["61-80"] += 1
        else:
            score_ranges["81-100"] += 1

    return {
        "total": len(candidates),
        "avg_score": avg_score,
        "top_skills": top_skills,
        "strong_matches": strong_matches,
        "status_counts": status_counts,
        "score_distribution": score_ranges
    }
