"""
AI Resume Analyzer — Flask API Server

Endpoints:
  Health:     GET  /health
  Analyze:    POST /analyze              (single resume)
  Bulk:       POST /analyze/bulk         (multiple resumes)
  Candidates: GET  /candidates           (list with filters)
  Candidate:  GET  /candidates/:id       (single)
  Delete:     DELETE /candidates/:id     (delete)
  Status:     PATCH /candidates/:id/status (shortlist/reject/maybe)
  Compare:    POST /compare              (side-by-side)
  Stats:      GET  /stats                (dashboard)
  Profiles:   GET  /profiles             (weight profiles)
  Export:     GET  /export/csv           (CSV download)
  Clear:      DELETE /clear              (dev only)
"""

import os
import uuid
import logging

from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

# ── Setup logging ────────────────────────────────────────────────
from utils.logger import setup_logging
setup_logging()
logger = logging.getLogger(__name__)

# ── Flask app ────────────────────────────────────────────────────
app = Flask(__name__)

# CORS — allow frontend origins
is_dev = os.getenv("APP_ENV") != "production" and os.getenv("FLASK_ENV") != "production"

if is_dev:
    CORS(app, supports_credentials=True) # Allow all in dev
else:
    CORS(app, origins="*")

# 10MB max upload size
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "..", "data", "resumes")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"pdf", "docx", "txt"}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ── Lazy imports (avoid circular / slow startup) ─────────────────
def _get_pipeline():
    from services.resume_parser import parse_resume
    from services.skill_extractor import (
        extract_skills, extract_skills_with_categories,
        extract_experience_years, extract_education
    )
    from services.job_matcher import (
        calculate_match_score, get_matched_skills,
        get_missing_skills, calculate_semantic_score,
        calculate_score_breakdown, generate_recommendation,
        get_weight_profiles
    )
    from services.explainer import generate_explanation
    from database.db_handler import save_candidate

    return {
        "parse_resume": parse_resume,
        "extract_skills": extract_skills,
        "extract_skills_with_categories": extract_skills_with_categories,
        "extract_experience_years": extract_experience_years,
        "extract_education": extract_education,
        "calculate_match_score": calculate_match_score,
        "get_matched_skills": get_matched_skills,
        "get_missing_skills": get_missing_skills,
        "calculate_semantic_score": calculate_semantic_score,
        "calculate_score_breakdown": calculate_score_breakdown,
        "generate_recommendation": generate_recommendation,
        "generate_explanation": generate_explanation,
        "save_candidate": save_candidate,
        "get_weight_profiles": get_weight_profiles,
    }


def _analyze_single_file(file, job_description, required_skills, required_experience, profile=None):
    """
    Core analysis pipeline for a single resume file.
    Returns (result_dict, error_string_or_None)
    """
    pipeline = _get_pipeline()

    # Save file with unique name
    file_ext = file.filename.rsplit(".", 1)[1].lower()
    unique_name = f"{uuid.uuid4().hex}.{file_ext}"
    file_path = os.path.join(UPLOAD_FOLDER, unique_name)
    file.save(file_path)

    try:
        # 1. Parse resume
        parsed = pipeline["parse_resume"](file_path)

        # Handle parse errors gracefully
        if parsed.get("parse_error"):
            logger.warning(f"Parse issue for {file.filename}: {parsed['parse_error']}")

        # 2. Extract skills
        clean_text = parsed.get("clean_text", "")
        skills_found = pipeline["extract_skills"](clean_text)
        skills_by_category = pipeline["extract_skills_with_categories"](clean_text)

        # 3. Experience and education
        experience_years = pipeline["extract_experience_years"](clean_text)
        education = pipeline["extract_education"](clean_text)

        # 4. Normalize required_skills through alias system for consistent matching
        from services.skill_extractor import normalize_skill
        normalized_required = [normalize_skill(s) for s in required_skills]

        # 5. Calculate scores
        keyword_score = pipeline["calculate_match_score"](skills_found, normalized_required)
        semantic_score = pipeline["calculate_semantic_score"](
            clean_text[:1000], job_description
        ) if job_description else 0.0

        score_breakdown = pipeline["calculate_score_breakdown"](
            keyword_score, semantic_score,
            experience_years, required_experience, profile
        )

        matched = pipeline["get_matched_skills"](skills_found, normalized_required)
        missing = pipeline["get_missing_skills"](skills_found, normalized_required)
        recommendation = pipeline["generate_recommendation"](score_breakdown["final_score"])

        # 6. Build result
        result = {
            "name": parsed.get("name", "Unknown Candidate"),
            "email": parsed.get("email"),
            "phone": parsed.get("phone"),
            "linkedin": parsed.get("linkedin"),
            "education": education,
            "confidence": parsed.get("confidence", "medium"),
            "parse_error": parsed.get("parse_error"),

            "final_score": score_breakdown["final_score"],
            "keyword_score": round(keyword_score, 1),
            "score_breakdown": score_breakdown,

            "skills_found": skills_found,
            "skills_by_category": skills_by_category,
            "skills_matched": len(matched),
            "skills_total": len(matched) + len(missing),
            "matched_skills": matched,
            "missing_skills": missing,

            "semantic_score": round(semantic_score, 1) if semantic_score else 0,
            "experience_years": experience_years,
            "required_experience": required_experience,
            "word_count": parsed.get("word_count", 0),
            "recommendation": recommendation,
            "resume_file": unique_name,

            # Pass raw text for dedup hashing (won't be stored)
            "clean_text": clean_text,
        }

        # 7. Generate explanation
        explanation = pipeline["generate_explanation"](result)
        result["explanation"] = explanation

        # 8. Save to database (dedup check happens inside)
        candidate_id, is_duplicate = pipeline["save_candidate"](result)
        result["id"] = candidate_id
        result["is_duplicate"] = is_duplicate

        # Remove clean_text from response (was only needed for dedup)
        result.pop("clean_text", None)

        if is_duplicate:
            result["duplicate_warning"] = f"This resume was already analyzed (existing ID: {candidate_id})"

        return result, None

    except Exception as e:
        logger.error(f"Analysis failed for {file.filename}: {e}", exc_info=True)
        return None, str(e)

    finally:
        # Clean up uploaded file to prevent disk filling
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError as cleanup_err:
            logger.warning(f"Could not clean up file {file_path}: {cleanup_err}")


# ══════════════════════════════════════════════════════════════════
# ── API ENDPOINTS ─────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════

# ── Health check ──────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "AI Resume Analyzer API is running"})


# ── Analyze a single resume ───────────────────────────────────────
@app.route("/analyze", methods=["POST"])
def analyze_resume():
    try:
        # Validate file
        if "resume" not in request.files:
            return jsonify({"error": "No resume file provided"}), 400

        file = request.files["resume"]
        if file.filename == "":
            return jsonify({"error": "No file selected"}), 400
        if not allowed_file(file.filename):
            return jsonify({"error": "File type not supported. Use PDF, DOCX, or TXT"}), 400

        # Get job description and requirements from form
        job_description = request.form.get("job_description", "")
        required_skills_raw = request.form.get("required_skills", "")
        required_experience = int(request.form.get("required_experience", "0"))
        profile = request.form.get("profile", None)

        required_skills = [s.strip() for s in required_skills_raw.split(",") if s.strip()]

        result, error = _analyze_single_file(
            file, job_description, required_skills, required_experience, profile
        )

        if error:
            return jsonify({"error": error}), 500

        return jsonify(result)

    except Exception as e:
        logger.error(f"Analyze endpoint error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ── Bulk upload ───────────────────────────────────────────────────
@app.route("/analyze/bulk", methods=["POST"])
def analyze_bulk():
    try:
        files = request.files.getlist("resumes")
        if not files:
            return jsonify({"error": "No resume files provided"}), 400

        job_description = request.form.get("job_description", "")
        required_skills_raw = request.form.get("required_skills", "")
        required_experience = int(request.form.get("required_experience", "0"))
        profile = request.form.get("profile", None)

        required_skills = [s.strip() for s in required_skills_raw.split(",") if s.strip()]

        results = []
        for file in files:
            if not file.filename or not allowed_file(file.filename):
                results.append({
                    "filename": file.filename or "unknown",
                    "status": "error",
                    "error": "Unsupported file type"
                })
                continue

            result, error = _analyze_single_file(
                file, job_description, required_skills, required_experience, profile
            )

            if error:
                results.append({
                    "filename": file.filename,
                    "status": "error",
                    "error": error
                })
            else:
                result["filename"] = file.filename
                result["status"] = "success"
                results.append(result)

        summary = {
            "total": len(files),
            "success": sum(1 for r in results if r.get("status") == "success"),
            "errors": sum(1 for r in results if r.get("status") == "error"),
        }

        return jsonify({"summary": summary, "results": results})

    except Exception as e:
        logger.error(f"Bulk analyze error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ── Get all candidates (with filtering) ──────────────────────────
@app.route("/candidates", methods=["GET"])
def get_candidates():
    try:
        from database.db_handler import get_all_candidates

        # Build filters from query params
        filters = {}
        for param in ["min_score", "max_score", "min_experience", "skills", "status"]:
            value = request.args.get(param)
            if value:
                filters[param] = value

        candidates = get_all_candidates(filters if filters else None)
        return jsonify(candidates)
    except Exception as e:
        logger.error(f"Get candidates error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ── Get single candidate ─────────────────────────────────────────
@app.route("/candidates/<candidate_id>", methods=["GET"])
def get_candidate(candidate_id):
    try:
        from database.db_handler import get_candidate_by_id
        candidate = get_candidate_by_id(candidate_id)
        if not candidate:
            return jsonify({"error": "Candidate not found"}), 404
        return jsonify(candidate)
    except Exception as e:
        logger.error(f"Get candidate error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ── Update candidate status ──────────────────────────────────────
@app.route("/candidates/<candidate_id>/status", methods=["PATCH"])
def update_status(candidate_id):
    try:
        from database.db_handler import update_candidate_status, VALID_STATUSES

        data = request.get_json()
        if not data or "status" not in data:
            return jsonify({"error": "Missing 'status' in request body"}), 400

        status = data["status"]
        if status not in VALID_STATUSES:
            return jsonify({
                "error": f"Invalid status. Must be one of: {', '.join(VALID_STATUSES)}"
            }), 400

        success = update_candidate_status(candidate_id, status)
        if success:
            return jsonify({"message": f"Status updated to '{status}'", "id": candidate_id})
        return jsonify({"error": "Candidate not found"}), 404
    except Exception as e:
        logger.error(f"Update status error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ── Delete candidate ─────────────────────────────────────────────
@app.route("/candidates/<candidate_id>", methods=["DELETE"])
def delete_candidate(candidate_id):
    try:
        from database.db_handler import delete_candidate
        success = delete_candidate(candidate_id)
        if success:
            return jsonify({"message": "Candidate deleted"})
        return jsonify({"error": "Candidate not found"}), 404
    except Exception as e:
        logger.error(f"Delete candidate error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ── Compare candidates ───────────────────────────────────────────
@app.route("/compare", methods=["POST"])
def compare_candidates():
    try:
        from database.db_handler import get_candidate_by_id

        data = request.get_json()
        if not data or "candidate_ids" not in data:
            return jsonify({"error": "Missing 'candidate_ids' in request body"}), 400

        candidate_ids = data["candidate_ids"]
        if len(candidate_ids) < 2:
            return jsonify({"error": "Need at least 2 candidate IDs to compare"}), 400

        candidates = []
        for cid in candidate_ids:
            candidate = get_candidate_by_id(cid)
            if not candidate:
                return jsonify({"error": f"Candidate '{cid}' not found"}), 404
            candidates.append(candidate)

        # Build comparison
        comparison = []
        for c in candidates:
            comparison.append({
                "id": c.get("id"),
                "name": c.get("name"),
                "final_score": c.get("final_score", 0),
                "skills_matched": c.get("skills_matched", 0),
                "skills_total": c.get("skills_total", 0),
                "experience_years": c.get("experience_years", 0),
                "education": c.get("education", "Not specified"),
                "score_breakdown": c.get("score_breakdown", {}),
                "matched_skills": c.get("matched_skills", []),
                "missing_skills": c.get("missing_skills", []),
                "recommendation": c.get("recommendation", {}),
                "explanation": c.get("explanation", {}),
            })

        # Sort by score to determine winner
        comparison.sort(key=lambda x: x["final_score"], reverse=True)

        # Generate recommendation
        top = comparison[0]
        runner_up = comparison[1] if len(comparison) > 1 else None
        score_diff = round(top["final_score"] - (runner_up["final_score"] if runner_up else 0), 1)

        if score_diff >= 15:
            rec_msg = f"{top['name']} is clearly the stronger candidate (+{score_diff} points)"
        elif score_diff >= 5:
            rec_msg = f"{top['name']} has a slight edge (+{score_diff} points), but both are strong"
        else:
            rec_msg = f"Very close match — consider other factors beyond the score"

        return jsonify({
            "candidates": comparison,
            "recommendation": {
                "best_candidate": top["id"],
                "best_name": top["name"],
                "score_difference": score_diff,
                "message": rec_msg
            }
        })

    except Exception as e:
        logger.error(f"Compare error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ── Dashboard stats ───────────────────────────────────────────────
@app.route("/stats", methods=["GET"])
def get_stats():
    try:
        from database.db_handler import get_stats
        stats = get_stats()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Stats error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ── Weight profiles ───────────────────────────────────────────────
@app.route("/profiles", methods=["GET"])
def list_profiles():
    try:
        from services.job_matcher import get_weight_profiles
        profiles = get_weight_profiles()
        return jsonify(profiles)
    except Exception as e:
        logger.error(f"Profiles error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ── CSV export ────────────────────────────────────────────────────
@app.route("/export/csv", methods=["GET"])
def export_csv():
    try:
        from database.db_handler import get_all_candidates
        from services.export import export_candidates_csv

        # Support filtering the export
        filters = {}
        for param in ["min_score", "max_score", "min_experience", "skills", "status"]:
            value = request.args.get(param)
            if value:
                filters[param] = value

        candidates = get_all_candidates(filters if filters else None)
        csv_data = export_candidates_csv(candidates)

        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=candidates_export.csv"}
        )
    except Exception as e:
        logger.error(f"CSV export error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ── Clear all (dev only — blocked in production) ─────────────────
@app.route("/clear", methods=["DELETE"])
def clear_all():
    # Block this destructive endpoint in production
    if os.getenv("APP_ENV") == "production" or os.getenv("FLASK_ENV") == "production":
        return jsonify({"error": "Clear is disabled in production"}), 403
    try:
        from database.db_handler import clear_all_candidates
        clear_all_candidates()
        return jsonify({"message": "All candidates cleared"})
    except Exception as e:
        logger.error(f"Clear error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════
# ── Auth endpoints ────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════

@app.route("/auth/signup", methods=["POST"])
def signup():
    try:
        from utils.auth import create_user, create_token

        data = request.get_json()
        if not data or not data.get("email") or not data.get("password"):
            return jsonify({"error": "Email and password are required"}), 400

        email = data["email"].strip().lower()
        password = data["password"]
        name = data.get("name", "").strip()

        if len(password) < 6:
            return jsonify({"error": "Password must be at least 6 characters"}), 400

        user, error = create_user(email, password, name)
        if error:
            return jsonify({"error": error}), 400

        token = create_token(user["id"], user["email"])
        return jsonify({"user": user, "token": token}), 201

    except Exception as e:
        logger.error(f"Signup error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/auth/login", methods=["POST"])
def login():
    try:
        from utils.auth import authenticate_user, create_token

        data = request.get_json()
        if not data or not data.get("email") or not data.get("password"):
            return jsonify({"error": "Email and password are required"}), 400

        email = data["email"].strip().lower()
        password = data["password"]

        user, error = authenticate_user(email, password)
        if error:
            return jsonify({"error": error}), 401

        token = create_token(user["id"], user["email"])
        return jsonify({"user": user, "token": token})

    except Exception as e:
        logger.error(f"Login error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════
# ── Error handlers ────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════

@app.errorhandler(413)
def too_large(e):
    return jsonify({"error": "File too large. Maximum size is 10MB."}), 413


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Endpoint not found"}), 404


@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "Internal server error"}), 500


# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    is_dev = os.getenv("APP_ENV") != "production" and os.getenv("FLASK_ENV") != "production"
    logger.info(f"AI Resume Analyzer API starting on http://localhost:{port} (debug={is_dev})")
    app.run(debug=is_dev, port=port)
