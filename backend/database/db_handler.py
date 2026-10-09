import json
import os
import uuid
from datetime import datetime

# ── Try MongoDB, fall back to local JSON ─────────────────────
MONGO_URI = os.getenv("MONGO_URI", "")
DB_NAME = os.getenv("DB_NAME", "resume_analyzer")
LOCAL_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "candidates.json")

_collection = None
_use_mongo = False


def _get_collection():
    global _collection, _use_mongo
    if _collection:
        return _collection
    if MONGO_URI:
        try:
            from pymongo import MongoClient
            client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
            client.server_info()
            _collection = client[DB_NAME]["candidates"]
            _use_mongo = True
            print("✅ Connected to MongoDB")
            return _collection
        except Exception as e:
            print(f"⚠️  MongoDB unavailable ({e}), using local JSON storage")
    return None


def _load_local():
    if not os.path.exists(LOCAL_DB_PATH):
        return []
    with open(LOCAL_DB_PATH, "r") as f:
        return json.load(f)


def _save_local(records):
    os.makedirs(os.path.dirname(LOCAL_DB_PATH), exist_ok=True)
    with open(LOCAL_DB_PATH, "w") as f:
        json.dump(records, f, indent=2, default=str)


# ── Public API ────────────────────────────────────────────────

def save_candidate(data: dict) -> str:
    """Save a candidate record. Returns the generated ID."""
    candidate_id = str(uuid.uuid4())[:8]
    record = {
        "id": candidate_id,
        "created_at": datetime.utcnow().isoformat(),
        "status": None,  # shortlisted / rejected / maybe / None
        **data
    }
    col = _get_collection()
    if col is not None:
        col.insert_one(record)
    else:
        records = _load_local()
        records.append(record)
        _save_local(records)
    return candidate_id


def get_all_candidates():
    """Return all candidates sorted by score descending."""
    col = _get_collection()
    if col is not None:
        results = list(col.find({}, {"_id": 0}).sort("final_score", -1))
    else:
        results = _load_local()
        results.sort(key=lambda x: x.get("final_score", 0), reverse=True)
    return results


def get_candidate_by_id(candidate_id: str):
    """Return a single candidate by ID."""
    col = _get_collection()
    if col is not None:
        return col.find_one({"id": candidate_id}, {"_id": 0})
    else:
        for r in _load_local():
            if r.get("id") == candidate_id:
                return r
    return None


def update_candidate_status(candidate_id: str, status: str) -> bool:
    """Update shortlist status for a candidate. Status: shortlisted, rejected, maybe, or null."""
    valid_statuses = {"shortlisted", "rejected", "maybe", None, "null"}
    if status == "null":
        status = None
    if status not in valid_statuses:
        return False

    col = _get_collection()
    if col is not None:
        result = col.update_one({"id": candidate_id}, {"$set": {"status": status}})
        return result.modified_count > 0
    else:
        records = _load_local()
        for r in records:
            if r.get("id") == candidate_id:
                r["status"] = status
                _save_local(records)
                return True
    return False


def get_candidates_by_status(status: str):
    """Return candidates filtered by status."""
    col = _get_collection()
    if col is not None:
        return list(col.find({"status": status}, {"_id": 0}).sort("final_score", -1))
    else:
        records = _load_local()
        filtered = [r for r in records if r.get("status") == status]
        filtered.sort(key=lambda x: x.get("final_score", 0), reverse=True)
        return filtered


def delete_candidate(candidate_id: str) -> bool:
    """Delete a candidate by ID."""
    col = _get_collection()
    if col is not None:
        result = col.delete_one({"id": candidate_id})
        return result.deleted_count > 0
    else:
        records = _load_local()
        new_records = [r for r in records if r.get("id") != candidate_id]
        if len(new_records) < len(records):
            _save_local(new_records)
            return True
    return False


def clear_all_candidates():
    """Delete all candidates (for testing)."""
    col = _get_collection()
    if col is not None:
        col.delete_many({})
    else:
        _save_local([])
