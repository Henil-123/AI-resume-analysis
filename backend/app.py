import os
import uuid
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
CORS(app)


@app.route("/")
def serve_frontend():
    return send_from_directory(FRONTEND_DIR, "index.html")

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "..", "data", "resumes")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"pdf", "docx", "doc", "txt"}
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "10"))


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _resolve_required_skills(required_skills, job_description):
    """Normalize user skills; auto-extract from JD when none provided."""
    from services.skill_aliases import normalize_skill_list
    cleaned = normalize_skill_list(required_skills)
    auto_extracted = False
    if not cleaned and job_description:
        from services.skill_extractor import extract_required_skills_from_jd
        cleaned = extract_required_skills_from_jd(job_description)
        auto_extracted = True
    return cleaned, auto_extracted


def _load_jd_from_request():
    """Unify JD input: optional jd_file (pdf/docx/txt) + textarea.

    File text wins as the base; pasted textarea is appended.
    Returns (jd_text, jd_meta_dict).
    """
    pasted = (request.form.get("job_description", "") or "").strip()
    jd_file = request.files.get("jd_file")
    # JSON fallback (used by /parse-jd with JSON body)
    if not pasted and not jd_file and request.is_json:
        try:
            pasted = ((request.get_json() or {}).get("job_description", "") or "").strip()
        except Exception:
            pass
    if jd_file and jd_file.filename:
        if not allowed_file(jd_file.filename):
            raise ValueError("JD file type not supported. Use PDF, DOCX, or TXT")
        ext = jd_file.filename.rsplit(".", 1)[1].lower()
        tmp = os.path.join(UPLOAD_FOLDER, f"jd_{uuid.uuid4().hex}.{ext}")
        jd_file.save(tmp)
        if os.path.getsize(tmp) > MAX_FILE_MB * 1024 * 1024:
            os.remove(tmp)
            raise ValueError(f"JD file exceeds {MAX_FILE_MB}MB limit")
        from services.jd_parser import parse_jd_file
        meta = parse_jd_file(tmp)
        jd_text = meta.get("job_description", "")
        if pasted:
            jd_text = (jd_text + "\n" + pasted).strip()
            # Re-parse combined text so weights/sections cover the pasted part too
            from services.jd_parser import parse_jd_text as _parse_combined
            meta = _parse_combined(jd_text)
        try:
            os.remove(tmp)
        except Exception:
            pass
        return jd_text, meta
    if pasted:
        from services.jd_parser import parse_jd_text
        return pasted, parse_jd_text(pasted)
    return "", {}


def _build_scoring_weights(required_skills, jd_meta):
    """Merge JD section weights with any user-typed skills (weight 1.0)."""
    from services.skill_aliases import normalize_skill_list
    weights = dict((jd_meta or {}).get("skill_weights") or {})
    for s in normalize_skill_list(required_skills):
        weights.setdefault(s, 1.0)
    if not weights:
        weights = {s: 1.0 for s in normalize_skill_list(required_skills)}
    return weights


def _extract_resume(file_path):
    """Pass 1: parse + extract everything measurable from a resume file."""
    from services.resume_parser import parse_resume
    from services.skill_extractor import (
        extract_skills, extract_skills_with_categories,
        extract_experience_years, extract_education
    )
    parsed = parse_resume(file_path)
    if parsed.get("word_count", 0) == 0:
        raise ValueError("No readable text extracted — file is empty or unreadable")
    skills_found = extract_skills(parsed["clean_text"])
    return {
        "parsed": parsed,
        "skills_found": skills_found,
        "skills_by_category": extract_skills_with_categories(parsed["clean_text"]),
        "experience_years": extract_experience_years(parsed["clean_text"]),
        "education": extract_education(parsed["clean_text"]),
        "resume_file": os.path.basename(file_path),
    }


def _score_extracted(ext, required_skills, required_experience, job_description,
                     skill_weights=None, idf=None, must_have=None):
    """Pass 2: precision-score an extracted resume against the JD."""
    from services.job_matcher import (
        calculate_semantic_score,
        calculate_weighted_skill_score,
        calculate_final_score, generate_recommendation,
        calculate_skills_match_pct, calculate_experience_match_pct,
        generate_strengths, generate_weaknesses, generate_resume_summary,
        explain_score, fingerprint_candidate
    )
    parsed = ext["parsed"]
    skills_found = ext["skills_found"]
    experience_years = ext["experience_years"]
    education = ext["education"]
    must_have = [m.lower().strip() for m in (must_have or [])]

    # 1. Precision skill coverage (JD weights × pool IDF + fuzzy recovery)
    weighted = calculate_weighted_skill_score(skills_found, skill_weights, idf,
                                              resume_text=parsed["clean_text"])
    keyword_score = weighted["score"]
    matched, missing = weighted["matched"], weighted["missing"]
    critical_missing = [m for m in missing if m in must_have]

    # 2. Chunked semantic fit over the full JD
    semantic_score = calculate_semantic_score(
        parsed["clean_text"], job_description
    ) if job_description else 0.0

    final_score = calculate_final_score(
        keyword_score, semantic_score,
        experience_years, required_experience, education,
        must_have_missing=len(critical_missing)
    )
    recommendation = generate_recommendation(final_score)

    skills_match_pct = calculate_skills_match_pct(matched, required_skills)
    exp_match_pct = calculate_experience_match_pct(experience_years, required_experience)

    result = {
        "name": parsed["name"],
        "email": parsed["email"],
        "phone": parsed["phone"],
        "links": parsed.get("links", {}),
        "education": education,
        "experience_years": experience_years,
        "required_experience": required_experience,
        "required_skills": required_skills,
        "skills_auto_extracted": ext.get("skills_auto", False),
        "skills_found": skills_found,
        "skills_by_category": ext["skills_by_category"],
        "matched_skills": matched,
        "missing_skills": missing,
        "critical_missing": critical_missing,
        "fuzzy_matches": weighted["fuzzy_matches"],
        "keyword_score": keyword_score,
        "semantic_score": semantic_score,
        "final_score": final_score,
        "score_breakdown": explain_score(
            keyword_score, semantic_score,
            experience_years, required_experience, education,
            must_have_missing=len(critical_missing)
        ),
        "fingerprint": fingerprint_candidate(parsed["name"], parsed["email"], parsed["phone"]),
        "skills_match_pct": skills_match_pct,
        "exp_match_pct": exp_match_pct,
        "recommendation": recommendation,
        "word_count": parsed["word_count"],
        "resume_file": ext["resume_file"],
    }
    result["strengths"] = generate_strengths(result, required_skills)
    result["weaknesses"] = generate_weaknesses(result, required_skills)
    result["summary"] = generate_resume_summary(result)
    return result


def _analyze_single_file(file_path, required_skills, required_experience, job_description,
                         skill_weights=None, idf=None, must_have=None, jd_meta=None):
    """Core analysis logic for a single resume file. Returns result dict."""
    required_skills, skills_auto = _resolve_required_skills(required_skills, job_description)
    if skill_weights is None:
        skill_weights = _build_scoring_weights(required_skills, jd_meta)
    if must_have is None:
        must_have = (jd_meta or {}).get("must_have_skills") or []
    ext = _extract_resume(file_path)
    ext["skills_auto"] = skills_auto
    result = _score_extracted(ext, required_skills, required_experience,
                              job_description, skill_weights, idf, must_have)
    return result


# ── Health check ──────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    info = {"status": "ok", "message": "AI Resume Analyzer API is running"}
    try:
        from services.skill_extractor import SPACY_MODEL_NAME
        info["spacy_model"] = SPACY_MODEL_NAME
    except Exception as e:
        info["spacy_model"] = f"unavailable: {e}"
    try:
        from services.job_matcher import embed_status
        info["embed_model"] = embed_status()
    except Exception as e:
        info["embed_model"] = f"unavailable: {e}"
    return jsonify(info)


# ── Analyze a single resume ───────────────────────────────────
@app.route("/analyze", methods=["POST"])
def analyze_resume():
    try:
        if "resume" not in request.files:
            return jsonify({"error": "No resume file provided"}), 400

        file = request.files["resume"]
        if file.filename == "":
            return jsonify({"error": "No file selected"}), 400
        if not allowed_file(file.filename):
            return jsonify({"error": "File type not supported. Use PDF, DOCX, or TXT"}), 400

        job_description, jd_meta = _load_jd_from_request()
        required_skills_raw = request.form.get("required_skills", "")
        required_experience_raw = request.form.get("required_experience", "0")
        try:
            required_experience = int(required_experience_raw or "0")
        except ValueError:
            required_experience = 0
        # Auto-fill experience from JD file when user left it at 0
        if required_experience == 0 and jd_meta.get("min_experience"):
            required_experience = jd_meta["min_experience"]
        required_skills = [s.strip() for s in required_skills_raw.split(",") if s.strip()]

        # Save file
        file_ext = file.filename.rsplit(".", 1)[1].lower()
        unique_name = f"{uuid.uuid4().hex}.{file_ext}"
        file_path = os.path.join(UPLOAD_FOLDER, unique_name)
        file.save(file_path)

        # Analyze
        result = _analyze_single_file(file_path, required_skills, required_experience, job_description,
                                      jd_meta=jd_meta)
        result["jd_understanding"] = {
            "job_title": jd_meta.get("job_title", ""),
            "summary": jd_meta.get("summary", ""),
            "key_points": jd_meta.get("key_points", []),
            "min_experience": jd_meta.get("min_experience", 0),
        }

        # Save to DB
        from database.db_handler import save_candidate
        candidate_id = save_candidate(result)
        result["id"] = candidate_id

        return jsonify(result)

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ── Batch analyze multiple resumes ────────────────────────────
@app.route("/analyze-batch", methods=["POST"])
def analyze_batch():
    try:
        files = request.files.getlist("resumes")
        if not files:
            return jsonify({"error": "No resume files provided"}), 400

        job_description, jd_meta = _load_jd_from_request()
        required_skills_raw = request.form.get("required_skills", "")
        required_experience_raw = request.form.get("required_experience", "0")
        try:
            required_experience = int(required_experience_raw or "0")
        except ValueError:
            required_experience = 0
        if required_experience == 0 and jd_meta.get("min_experience"):
            required_experience = jd_meta["min_experience"]
        required_skills = [s.strip() for s in required_skills_raw.split(",") if s.strip()]

        # Resolve once so auto-extracted JD skills are consistent across batch
        required_skills, skills_auto = _resolve_required_skills(required_skills, job_description)
        skill_weights = _build_scoring_weights(required_skills, jd_meta)
        must_have = jd_meta.get("must_have_skills") or []

        results = []
        errors = []
        seen_fingerprints = set()

        # ── Pass 1: save + extract every resume ──
        extracted = []
        for file in files:
            if file.filename == "":
                continue
            if not allowed_file(file.filename):
                errors.append({"file": file.filename, "error": "Unsupported file type"})
                continue

            try:
                file_ext = file.filename.rsplit(".", 1)[1].lower()
                unique_name = f"{uuid.uuid4().hex}.{file_ext}"
                file_path = os.path.join(UPLOAD_FOLDER, unique_name)
                file.save(file_path)

                # Size guard after save
                if os.path.getsize(file_path) > MAX_FILE_MB * 1024 * 1024:
                    os.remove(file_path)
                    errors.append({"file": file.filename,
                                   "error": f"File exceeds {MAX_FILE_MB}MB limit"})
                    continue

                ext = _extract_resume(file_path)
                ext["skills_auto"] = skills_auto
                ext["original_filename"] = file.filename
                extracted.append(ext)
            except Exception as fe:
                errors.append({"file": file.filename, "error": str(fe)})

        # ── Pool IDF: skills rare among THESE applicants weigh more ──
        from services.job_matcher import compute_idf
        idf = compute_idf([set(e["skills_found"]) for e in extracted])

        # ── Pass 2: precision-score each resume against the JD ──
        for ext in extracted:
            try:
                result = _score_extracted(ext, required_skills, required_experience,
                                          job_description, skill_weights, idf, must_have)

                # Duplicate detection within batch + against DB fingerprints
                fp = result.get("fingerprint")
                result["is_duplicate"] = bool(fp and fp in seen_fingerprints)
                if fp:
                    seen_fingerprints.add(fp)

                from database.db_handler import save_candidate
                candidate_id = save_candidate(result)
                result["id"] = candidate_id
                result["original_filename"] = ext["original_filename"]
                results.append(result)
            except Exception as fe:
                errors.append({"file": ext.get("original_filename", "?"), "error": str(fe)})

        # Sort by score descending
        results.sort(key=lambda x: x.get("final_score", 0), reverse=True)

        return jsonify({
            "candidates": results,
            "total": len(results),
            "errors": errors,
            "required_skills": required_skills,
            "skills_auto_extracted": skills_auto,
            "jd_understanding": {
                "job_title": jd_meta.get("job_title", ""),
                "summary": jd_meta.get("summary", ""),
                "key_points": jd_meta.get("key_points", []),
                "min_experience": jd_meta.get("min_experience", 0),
                "education_required": jd_meta.get("education_required", ""),
                "word_count": jd_meta.get("word_count", 0),
            },
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ── Get all candidates ────────────────────────────────────────
@app.route("/candidates", methods=["GET"])
def get_candidates():
    try:
        from database.db_handler import get_all_candidates
        candidates = get_all_candidates()
        return jsonify(candidates)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Get single candidate ──────────────────────────────────────
@app.route("/candidates/<candidate_id>", methods=["GET"])
def get_candidate(candidate_id):
    try:
        from database.db_handler import get_candidate_by_id
        candidate = get_candidate_by_id(candidate_id)
        if not candidate:
            return jsonify({"error": "Candidate not found"}), 404
        return jsonify(candidate)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Update candidate shortlist status ─────────────────────────
@app.route("/candidates/<candidate_id>/status", methods=["PATCH"])
def update_status(candidate_id):
    try:
        data = request.get_json()
        if not data or "status" not in data:
            return jsonify({"error": "Status field required"}), 400

        status = data["status"]
        from database.db_handler import update_candidate_status
        success = update_candidate_status(candidate_id, status)

        if success:
            return jsonify({"message": f"Status updated to '{status}'", "status": status})
        return jsonify({"error": "Candidate not found or invalid status"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Compare two candidates ────────────────────────────────────
@app.route("/compare", methods=["POST"])
def compare():
    try:
        data = request.get_json()
        if not data or "id1" not in data or "id2" not in data:
            return jsonify({"error": "Both id1 and id2 are required"}), 400

        from database.db_handler import get_candidate_by_id
        c1 = get_candidate_by_id(data["id1"])
        c2 = get_candidate_by_id(data["id2"])

        if not c1 or not c2:
            return jsonify({"error": "One or both candidates not found"}), 404

        from services.job_matcher import compare_candidates
        comparison = compare_candidates(c1, c2)
        return jsonify(comparison)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Parse JD file/text into structured understanding ─────────
@app.route("/parse-jd", methods=["POST"])
def parse_jd():
    """Accept jd_file (pdf/docx/txt) and/or job_description text + required_skills override.

    Returns the JD understanding: title, skills, min experience,
    education, key points, and a plain-English summary.
    """
    try:
        jd_text, jd_meta = _load_jd_from_request()
        if not jd_text:
            return jsonify({"error": "Provide jd_file or job_description"}), 400
        required_skills_raw = request.form.get("required_skills", "")
        if required_skills_raw:
            from services.skill_aliases import normalize_skill_list
            jd_meta["required_skills"] = normalize_skill_list(
                [s.strip() for s in required_skills_raw.split(",") if s.strip()])
        return jsonify(jd_meta)
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ── Extract skills from JD (for auto-fill UI) ────────────────
@app.route("/extract-jd-skills", methods=["POST"])
def extract_jd_skills():
    try:
        data = request.get_json() or {}
        jd = data.get("job_description", "")
        if not jd:
            return jsonify({"error": "job_description required"}), 400
        from services.skill_extractor import extract_required_skills_from_jd
        skills = extract_required_skills_from_jd(jd)
        return jsonify({"skills": skills, "count": len(skills)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Dashboard stats ───────────────────────────────────────────
@app.route("/stats", methods=["GET"])
def stats():
    try:
        from database.db_handler import get_all_candidates
        from collections import Counter
        cands = get_all_candidates()
        scores = [c.get("final_score", 0) for c in cands]
        skill_counter = Counter()
        for c in cands:
            for s in c.get("skills_found", []):
                skill_counter[s] += 1
        dist = {"strong": sum(1 for s in scores if s >= 80),
                "good": sum(1 for s in scores if 60 <= s < 80),
                "partial": sum(1 for s in scores if 40 <= s < 60),
                "weak": sum(1 for s in scores if s < 40)}
        return jsonify({
            "total": len(cands),
            "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
            "distribution": dist,
            "top_skills": dict(skill_counter.most_common(15)),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Delete candidate ──────────────────────────────────────────
@app.route("/candidates/<candidate_id>", methods=["DELETE"])
def delete_candidate(candidate_id):
    try:
        from database.db_handler import delete_candidate
        success = delete_candidate(candidate_id)
        if success:
            return jsonify({"message": "Candidate deleted"})
        return jsonify({"error": "Candidate not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Clear all (dev only) ──────────────────────────────────────
@app.route("/clear", methods=["DELETE"])
def clear_all():
    try:
        from database.db_handler import clear_all_candidates
        clear_all_candidates()
        return jsonify({"message": "All candidates cleared"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"\n>> AI Resume Analyzer API running on http://localhost:{port}\n")
    app.run(debug=True, port=port)
