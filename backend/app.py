import logging
import os
import uuid
from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

load_dotenv()

import app_config as app_config
from rate_limit import limit as rate_limit

log = logging.getLogger("resume_analyzer")

# Process-local read-through cache. The database (database/store.py) is
# authoritative: every write goes to the store first, every read falls back
# to it, so records survive restarts and are shared between instances.
_ANALYSES = {}

_STORE = None


def _store():
    """Return the persistent analysis store, initialising it on first use."""
    global _STORE
    if _STORE is None:
        from database.migrations import upgrade_database
        from database.store import AnalysisStore
        engine, version = upgrade_database(app_config.DATABASE_URL)
        _STORE = AnalysisStore(app_config.DATABASE_URL, engine=engine)
        log.info("analysis store ready (schema v%s)", version)
    return _STORE


def _rebind_store(database_url):
    """Test hook: point the store at a throwaway database."""
    global _STORE
    from database.migrations import upgrade_database
    from database.store import AnalysisStore
    engine, _ = upgrade_database(database_url)
    _STORE = AnalysisStore(database_url, engine=engine)
    return _STORE


def _get_stored_analysis(analysis_id):
    """Memory cache first, persistent store second. Returns payload or None."""
    a = _ANALYSES.get(analysis_id)
    if a:
        return a
    try:
        payload = _store().get_analysis(analysis_id)
    except Exception as e:
        log.error("store read failed: %s", type(e).__name__)
        return None
    if payload is None:
        # Legacy fallback: pre-store candidate records (db_handler).
        try:
            from database.db_handler import get_candidate_by_id
            c = get_candidate_by_id(analysis_id)
            if not c:
                return None
            payload = {
                "candidate": {k: v for k, v in c.items() if k != "explainable"},
                "explainable": c.get("explainable", {}),
                "jd_meta": c.get("jd_meta", {}),
                "job_description": c.get("job_description", ""),
                "required_experience": c.get("required_experience", 0),
                "resume_file": c.get("resume_file", ""),
            }
        except Exception:
            return None
    _ANALYSES[analysis_id] = payload
    return payload


def _warmup_models():
    """Pre-load spaCy + the embedding engine in the background at startup.

    Without this, the first analysis after a cold start pays ~30-60s of model
    loading inside the request, which trips the hosting proxy timeout and the
    user sees 'cannot connect'. Warm models make the first request fast.
    Runs once per process; failures only log (requests still work, slower).
    """
    import threading
    import time

    def _run():
        t0 = time.time()
        try:
            from services import job_matcher as _jm
            _jm._get_embed_model()
            _jm._embed_batch(["warmup probe"])
            import services.skill_extractor  # noqa: F401 (spacy + matcher)
            log.info("model warmup done in %.1fs (engine=%s)",
                     time.time() - t0, _jm.embed_status())
        except Exception as e:
            log.warning("model warmup failed (%s); first request will be slower",
                        type(e).__name__)
    threading.Thread(target=_run, name="model-warmup", daemon=True).start()


_warmup_models()  # covers `python app.py` dev; gunicorn --preload forks below


def _is_prod():
    return app_config.IS_PROD


def _server_error(e, where):
    """Log fully server-side; never leak stack traces to clients in prod."""
    log.exception("%s failed", where)
    if _is_prod():
        return jsonify({"error": "Internal error. The incident was logged."}), 500
    return jsonify({"error": str(e)}), 500


FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
if app_config.CORS_ORIGINS:
    CORS(app, origins=app_config.CORS_ORIGINS)
elif _is_prod():
    # No origins configured: allow all so the hosted frontend works out of
    # the box (the API carries no cookies; review/delete stay token-gated).
    # Loud on purpose — set CORS_ORIGINS to lock down. validate() says the
    # same when run via `python app.py`.
    log.warning("CORS_ORIGINS unset in production: allowing all origins. "
                "Set CORS_ORIGINS to your frontend URL to lock down.")
    CORS(app)
else:
    CORS(app)
app.config["MAX_CONTENT_LENGTH"] = app_config.MAX_FILE_MB * 1024 * 1024


# NOTE: no eager in-worker warmup here. An earlier revision warmed models via
# @app.before_request, but on the 512MB free tier the warmup thread raced the
# request thread into a double spaCy load and OOM-killed the single worker in
# a crash loop (service hung with zero-byte responses). Import-time
# _warmup_models() above covers `python app.py` dev; under gunicorn --preload
# its thread harmlessly dies on fork and models load lazily, once, on demand.


@app.errorhandler(413)
def _too_large(_e):
    return jsonify({"error": f"Upload exceeds {app_config.MAX_FILE_MB}MB limit"}), 413


@app.route("/")
def serve_frontend():
    return send_from_directory(FRONTEND_DIR, "index.html")

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "..", "data", "resumes")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"pdf", "docx", "doc", "txt"}
MAX_FILE_MB = app_config.MAX_FILE_MB


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def allowed_file(filename):
    name = secure_filename(filename or "")
    return "." in name and name.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _sniff_upload(path, ext):
    """Verify magic bytes match the claimed extension (extension+MIME, neither
    trusted alone). Raises ValueError on mismatch, encrypted PDFs, or empty
    files. Encrypted PDFs are rejected with 422 guidance, not scored as zero.
    """
    with open(path, "rb") as f:
        head = f.read(8)
    if len(head) == 0:
        raise ValueError("File is empty (0 bytes)")
    if ext == "pdf":
        if head[:5] != b"%PDF-":
            raise ValueError("File claims .pdf but lacks a PDF header")
        try:
            import PyPDF2
            with open(path, "rb") as f:
                rd = PyPDF2.PdfReader(f)
                if getattr(rd, "is_encrypted", False):
                    raise ValueError("PDF is encrypted; upload an unprotected copy")
        except ValueError:
            raise
        except Exception:
            pass  # header check already passed; extraction handles the rest
    elif ext in ("docx", "doc"):
        if ext == "docx" and head[:2] != b"PK":
            raise ValueError("File claims .docx but lacks a ZIP header")
    elif ext == "txt":
        try:
            with open(path, "r", encoding="utf-8") as f:
                f.read(65536)
        except UnicodeDecodeError:
            raise ValueError("Text file is not valid UTF-8")
    return True


def _check_request_size():
    """Reject oversized bodies before parsing (Flask raises 413 inside the
    view's try block otherwise, which generic handlers would mask as 500)."""
    limit = app.config["MAX_CONTENT_LENGTH"]
    if request.content_length and request.content_length > limit:
        return jsonify(
            {"error": f"Upload exceeds {app_config.MAX_FILE_MB}MB limit"}), 413
    return None


def _normalize(skill):
    from services.skill_aliases import normalize_skill
    return normalize_skill(skill)


def _prompt_injection_note(*texts):
    """Flag prompt-injection-looking content as data (never executed)."""
    import re
    pat = re.compile(r"(ignore (previous|all) instructions|system prompt|jailbreak|"
                     r"you are now|disregard (the|your) instructions)", re.I)
    for t in texts:
        if t and pat.search(t):
            return ("Upload contains instruction-like text; treated strictly as data, "
                    "never executed. Scores use deterministic + embedding signals only.")
    return ""


def _run_explainable(parsed, skills_found, experience_years, education,
                     job_description, jd_meta, required_skills,
                     required_experience, fuzzy_hits=None, weights_override=None,
                     experience_profile=None):
    """Full explainable pipeline (requirements -> evidence -> R_i -> S -> report).

    Raises ValueError on insufficient data; never fabricates model output.
    """
    from services.requirement_extractor import extract_requirements
    from services.evidence_extractor import extract_evidence
    from services.scoring_engine import analyze as engine_analyze, get_config
    from services.eligibility_checker import check_eligibility
    from services.report_generator import build_report, analyze_gaps

    requirements = extract_requirements(job_description or "", jd_meta)
    if not requirements:
        raise ValueError("insufficient-data: no scoreable requirements extracted from JD")
    # Evidence must be gathered for the JD's REQUIREMENTS, not just the skills
    # the resume happens to mention. Passing only skills_found meant a
    # requirement like "SQL" got no evidence record when the resume listed
    # "MySQL", so P_i fell to 0 even though supporting text existed.
    want_skills = sorted({_normalize(r.get("canonical_skill"))
                          for r in requirements if r.get("canonical_skill")}
                         | {_normalize(s) for s in (skills_found or [])})
    evidence = extract_evidence(parsed, want_skills)
    analysis = engine_analyze(requirements, skills_found, evidence,
                              parsed.get("clean_text", ""), experience_years,
                              required_experience, fuzzy_hits,
                              weights_override=weights_override,
                              experience_profile=experience_profile)
    if analysis.get("status") != "ok":
        raise ValueError(analysis.get("reason", "insufficient-data"))
    req_scores = analysis["requirements"]
    exp_detail = (analysis.get("aggregate", {}).get("experience_detail") or {})
    eligibility = check_eligibility(requirements, evidence, education,
                                    experience_years, required_experience, jd_meta,
                                    jd_text=job_description or "")
    gaps = analyze_gaps(req_scores)
    inj = _prompt_injection_note(parsed.get("raw_text", ""), job_description or "")
    errors = ([inj] if inj else []) + (
        [f"semantic unavailable: {r['semantic_error']}" for r in req_scores
         if r.get("semantic_error")][:1])
    report = build_report(
        {"name": parsed.get("name"), "education": education,
         "experience_years": experience_years},
        requirements, req_scores, analysis["aggregate"], eligibility,
        evidence, jd_meta, gaps,
        extraction_status=parsed.get("extraction_status", "ok"),
        errors=errors)
    return {"requirements": requirements, "evidence": evidence[:100],
            "evidence_total": len(evidence), "requirement_scores": req_scores,
            "aggregate": analysis["aggregate"], "eligibility": eligibility,
            "gaps": gaps, "report": report,
            # Authoritative headline: identical to aggregate.overall_score.
            "overall_score": analysis["aggregate"].get("overall_score"),
            "score_components": {
                "keyword_coverage": analysis["aggregate"].get("keyword_coverage"),
                "semantic_relevance": analysis["aggregate"].get("semantic_relevance"),
                "experience_fit": analysis["aggregate"].get("experience_fit"),
            },
            "experience_detail": exp_detail,
            "formula_version": analysis.get("formula_version"),
            "config_version": analysis.get("config_version")}


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
        try:
            _sniff_upload(tmp, ext)
        except ValueError as ve:
            os.remove(tmp)
            raise
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
    # Experience and education need the RAW text: clean_text collapses newlines,
    # which destroys the section headers the extractor relies on to tell
    # employment apart from university and project dates.
    exp_years, exp_detail = extract_experience_years(parsed["raw_text"], detail=True)
    return {
        "parsed": parsed,
        "skills_found": skills_found,
        "skills_by_category": extract_skills_with_categories(parsed["clean_text"]),
        "experience_years": exp_years,
        "experience_detail": exp_detail,
        "education": extract_education(parsed["raw_text"]),
        "resume_file": os.path.basename(file_path),
    }


def _score_extracted(ext, required_skills, required_experience, job_description,
                     skill_weights=None, idf=None, must_have=None, jd_meta=None,
                     weights_override=None):
    """Pass 2: precision-score an extracted resume against the JD.

    Legacy precision score fields are always computed (existing JS contracts).
    The explainable engine (requirements/evidence/R_i/S/eligibility/report)
    is attached additively as result["explainable"]; if it fails, the legacy
    score still returns and the failure is recorded, never hidden.
    """
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

    legacy_final = calculate_final_score(
        keyword_score, semantic_score,
        experience_years, required_experience, education,
        must_have_missing=len(critical_missing)
    )

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
        # legacy blend retained for comparison/audit only (never headlined)
        "legacy_final_score": legacy_final,
        "final_score": legacy_final,
        "score_breakdown": explain_score(
            keyword_score, semantic_score,
            experience_years, required_experience, education,
            must_have_missing=len(critical_missing)
        ),
        "fingerprint": fingerprint_candidate(parsed["name"], parsed["email"], parsed["phone"]),
        "skills_match_pct": skills_match_pct,
        "exp_match_pct": exp_match_pct,
        "recommendation": generate_recommendation(legacy_final),
        "word_count": parsed["word_count"],
        "resume_file": ext["resume_file"],
    }
    result["strengths"] = generate_strengths(result, required_skills)
    result["weaknesses"] = generate_weaknesses(result, required_skills)
    result["summary"] = generate_resume_summary(result)
    # ── Explainable layer (additive; never breaks legacy contracts) ──
    try:
        if job_description:
            result["explainable"] = _run_explainable(
                parsed, skills_found, experience_years, education,
                job_description, jd_meta or {}, required_skills,
                required_experience, weighted["fuzzy_matches"],
                weights_override, experience_profile=ext.get("experience_detail"))
        else:
            result["explainable"] = {"status": "insufficient-data",
                                     "reason": "no job description provided"}
    except ValueError as ve:
        result["explainable"] = {"status": "insufficient-data", "reason": str(ve)}
    except Exception as e:
        result["explainable"] = {"status": "error",
                                 "reason": f"explainable engine failed: {e}"[:300]}

    # ── Single authoritative score ────────────────────────────────
    # The explainable engine owns the headline. The legacy 45/30/15/10 blend
    # stays available as `legacy_final_score` for comparison only. Previously
    # `final_score` came from the legacy blend while the report showed S, so
    # the UI could display two different "overall" numbers for one analysis.
    exp = result["explainable"]
    if exp.get("aggregate", {}).get("status") == "ok":
        result["final_score"] = exp["aggregate"]["overall_score"]
        result["score_kind"] = exp["aggregate"].get("formula_version", "explainable-v2")
        result["score_breakdown"] = {
            "keyword_score": exp["aggregate"]["keyword_coverage"],
            "semantic_score": exp["aggregate"]["semantic_relevance"],
            "experience_score": exp["aggregate"]["experience_fit"],
            "education_score": None,
            "must_have_penalty": 0.0,
            "requirement_weighted_score": exp["aggregate"]["requirement_weighted_score"],
            "formula": exp["aggregate"]["formula"],
            "formula_version": exp["aggregate"]["formula_version"],
            "config_version": exp["aggregate"]["config_version"],
            "weights": exp["aggregate"]["weights"],
            "formula_inputs": exp["aggregate"]["formula_inputs"],
        }
        result["recommendation"] = generate_recommendation(result["final_score"])
        result["strengths"] = generate_strengths(result, required_skills)
        result["weaknesses"] = generate_weaknesses(result, required_skills)
        result["summary"] = generate_resume_summary(result)
        # Expose the same report object the report endpoint serves, so the
        # headline, the UI and any export cannot disagree.
        result["report"] = exp.get("report")
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
                              job_description, skill_weights, idf, must_have,
                              jd_meta)
    return result


# ── Health check ──────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    info = {"status": "ok", "message": "AI Resume Analyzer API is running",
            "app_env": os.getenv("APP_ENV", "development"),
            "semantic_engine": os.getenv("SEMANTIC_ENGINE", "bert")}
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
    # Groq was retired in favor of the local precision engine (BERT/TF-IDF).
    # Keys kept for backward compatibility with older dashboards.
    info["groq_connected"] = False
    info["groq_error"] = "Groq retired; local precision engine in use"
    try:
        from services.scoring_engine import get_config, FORMULA_VERSION
        cfg = get_config()
        info["config_version"] = cfg.get("config_version")
        info["formula_version"] = FORMULA_VERSION
        info["primary_formula"] = "S=0.60*K+0.30*M+0.10*E; R_i=100*(0.35K_i+0.35M_i+0.30P_i)"
    except Exception as e:
        info["config_version"] = f"invalid: {e}"
    return jsonify(info)


# ═══════════════════════════════════════════════════════════
# Versioned explainable API (spec section 20)
# ═══════════════════════════════════════════════════════════
# Weights are accepted if they sum to 1.0 within this tolerance. 0.6+0.3+0.1
# is 0.9999999999999999 in IEEE-754, so an exact comparison rejected the
# documented defaults.
WEIGHT_SUM_TOLERANCE = 1e-6


def _v1_validate_weights(body):
    """Validate recalculation weights.

    Returns a complete {keyword, semantic, experience} dict (missing components
    keep their configured default), or None when no weights were supplied.
    Weights are NOT silently renormalised: a genuinely wrong sum is rejected
    with the offending values named.
    """
    w = (body or {}).get("weights")
    if w is None:
        return None
    if not isinstance(w, dict):
        raise ValueError("weights must be an object {keyword, semantic, experience}")
    from services.scoring_engine import get_config as _cfg
    defaults = {"keyword": .6, "semantic": .3, "experience": .1}
    try:
        ov = (_cfg().get("overall") or {})
        defaults = {"keyword": ov.get("keyword_weight", .6),
                    "semantic": ov.get("semantic_weight", .3),
                    "experience": ov.get("experience_weight", .1)}
    except Exception:
        pass
    unknown = [k for k in w if k not in defaults]
    if unknown:
        raise ValueError(f"unknown weight(s) {unknown}; expected keys "
                         f"{sorted(defaults)}")
    out = {}
    for k, v in w.items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"weight '{k}' must be a number, got {v!r}")
        if not (0.0 <= fv <= 1.0):
            raise ValueError(f"weight '{k}' must be in [0, 1], got {fv}")
        out[k] = fv
    merged = {k: out.get(k, defaults[k]) for k in defaults}
    total = sum(merged.values())
    if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise ValueError(
            "weights must sum to 1.0 (got %.6f from %s); adjust the values or "
            "omit a field to keep its default of %.2f"
            % (total, {k: round(v, 4) for k, v in merged.items()}, 1.0))
    return merged


def _v1_store_analysis(candidate_id, payload):
    """Persist to the database (authoritative) and refresh the cache.

    Returns the persistent analysis id. `candidate_id` links the legacy
    candidate record; the store id is collision-resistant (uuid4 hex).
    """
    try:
        analysis_id = _store().create_analysis(payload)
    except Exception as e:
        log.error("store create failed: %s", type(e).__name__)
        if _is_prod():
            raise
        # Dev-only: keep serving from memory so local work continues, but
        # say so loudly instead of pretending the record is durable.
        log.warning("DEV ONLY: serving analysis from memory; record is NOT durable")
        analysis_id = uuid.uuid4().hex
    payload = dict(payload)
    payload["id"] = analysis_id
    _ANALYSES[analysis_id] = payload
    return analysis_id


def _review_authorized():
    """Bearer token for review/destructive endpoints.

    Returns (ok, error_response). When REVIEW_API_TOKEN is unset the
    endpoints are dev/internal-only (production startup refuses this
    unless REVIEW_OPEN is explicit).
    """
    token = app_config.REVIEW_API_TOKEN
    if not token:
        if _is_prod() and not app_config.REVIEW_OPEN:
            return False, (jsonify({"error": "Review endpoint is disabled."}), 403)
        return True, None
    auth = (request.headers.get("Authorization") or "")
    if auth == f"Bearer {token}":
        return True, None
    return False, (jsonify({"error": "Unauthorized: valid bearer token required."}), 401)


@app.route("/api/v1/health", methods=["GET"])
def v1_health():
    resp = health()
    data = resp.get_json()
    data["api_version"] = "v1"
    try:
        from services import job_matcher as _jm
        data["embedding_dimension"] = (
            int(_jm._embed_batch(["health check"]).shape[1])
            if _jm.embed_status() else None)
    except Exception:
        data["embedding_dimension"] = None
    return jsonify(data)


@app.route("/api/v1/documents/resume", methods=["POST"])
def v1_upload_resume():
    """Upload + process a resume: extraction preview + evidence (no scoring)."""
    too_big = _check_request_size()
    if too_big:
        return too_big
    try:
        if "resume" not in request.files:
            return jsonify({"error": "No resume file provided"}), 400
        f = request.files["resume"]
        if not f.filename or not allowed_file(f.filename):
            return jsonify({"error": "File type not supported. Use PDF, DOCX, or TXT"}), 400
        ext = secure_filename(f.filename).rsplit(".", 1)[1].lower()
        path = os.path.join(UPLOAD_FOLDER, f"v1_{uuid.uuid4().hex}.{ext}")
        f.save(path)
        try:
            _sniff_upload(path, ext)
            from services.resume_parser import validate_upload
            validate_upload(path, MAX_FILE_MB)
            from services.resume_parser import parse_resume
            parsed = parse_resume(path)
            if parsed.get("word_count", 0) == 0:
                return jsonify({"error": "No readable text extracted — file is empty or unreadable",
                                "extraction_status": parsed.get("extraction_status"),
                                "ocr_hint": parsed.get("ocr_hint", "")}), 422
            from services.section_parser import parse_resume_sections
            from services.skill_extractor import (extract_skills, extract_skills_with_categories,
                                                  extract_experience_years, extract_education)
            from services.resume_parser import clean_text
            skills = extract_skills(parsed["clean_text"])
            return jsonify({
                "preview": parsed["raw_text"][:5000],
                "word_count": parsed["word_count"],
                "pages": len(parsed.get("pages", [])),
                "extraction_status": parsed.get("extraction_status", "ok"),
                "ocr_hint": parsed.get("ocr_hint", ""),
                "sections": list(parse_resume_sections(parsed["raw_text"]).keys()),
                "skills": skills,
                "skills_by_category": extract_skills_with_categories(parsed["clean_text"]),
                "experience_years": extract_experience_years(parsed["raw_text"]),
                "education": extract_education(parsed["clean_text"]),
                "email": parsed["email"], "phone": parsed["phone"], "name": parsed["name"],
            })
        finally:
            try:
                os.remove(path)
            except Exception:
                pass
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/v1/documents/jd", methods=["POST"])
def v1_upload_jd():
    """Upload/process a JD: structured requirements preview."""
    too_big = _check_request_size()
    if too_big:
        return too_big
    try:
        jd_text, jd_meta = _load_jd_from_request()
        if not jd_text:
            return jsonify({"error": "Provide jd_file or job_description"}), 400
        from services.requirement_extractor import extract_requirements
        reqs = extract_requirements(jd_text, jd_meta)
        return jsonify({"jd_understanding": jd_meta, "requirements": reqs,
                        "count": len(reqs)})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/v1/analyses", methods=["POST"])
def v1_analyze():
    """Run a resume-JD comparison; returns candidate + full explainable report."""
    too_big = _check_request_size()
    if too_big:
        return too_big
    try:
        if "resume" not in request.files:
            return jsonify({"error": "No resume file provided"}), 400
        f = request.files["resume"]
        if not f.filename or not allowed_file(f.filename):
            return jsonify({"error": "File type not supported. Use PDF, DOCX, or TXT"}), 400
        weights_override = _v1_validate_weights(request.form.to_dict() and
                                                (request.get_json(silent=True) or {}))
        # weights may arrive as form field "weights" (JSON string) or JSON body
        if weights_override is None and request.form.get("weights"):
            import json as _json
            try:
                weights_override = _v1_validate_weights(
                    {"weights": _json.loads(request.form.get("weights"))})
            except ValueError as ve:
                return jsonify({"error": str(ve)}), 400
        job_description, jd_meta = _load_jd_from_request()
        if not job_description:
            return jsonify({"error": "Provide job_description or jd_file"}), 400
        try:
            required_experience = int(request.form.get("required_experience", "0") or "0")
        except ValueError:
            required_experience = 0
        if required_experience == 0 and jd_meta.get("min_experience"):
            required_experience = jd_meta["min_experience"]
        required_skills = [s.strip() for s in
                           request.form.get("required_skills", "").split(",") if s.strip()]
        ext = secure_filename(f.filename).rsplit(".", 1)[1].lower()
        path = os.path.join(UPLOAD_FOLDER, f"v1_{uuid.uuid4().hex}.{ext}")
        f.save(path)
        try:
            _sniff_upload(path, ext)
            from services.resume_parser import validate_upload
            validate_upload(path, MAX_FILE_MB)
        except ValueError as ve:
            try:
                os.remove(path)
            except Exception:
                pass
            msg = str(ve)
            code = 422 if ("encrypted" in msg or "readable text" in msg
                           or "header" in msg or "UTF-8" in msg) else 400
            return jsonify({"error": msg}), code
        try:
            result = _analyze_single_file(path, required_skills, required_experience,
                                          job_description,
                                          _build_scoring_weights(
                                              *_resolve_required_skills(required_skills,
                                                                        job_description)[:1],
                                              jd_meta),
                                          None, jd_meta.get("must_have_skills") or [],
                                          jd_meta)
        finally:
            pass  # keep stored resume file for audit trail (same as legacy)
        if result.get("explainable", {}).get("status") == "error":
            pass  # legacy score still valid; error recorded in payload
        from database.db_handler import save_candidate
        # re-run scoring with weight override for recalculation correctness
        if weights_override:
            extd = _extract_resume(path)
            result = _score_extracted(
                extd, result["required_skills"], required_experience, job_description,
                _build_scoring_weights(result["required_skills"], jd_meta),
                None, jd_meta.get("must_have_skills") or [], jd_meta,
                weights_override)
            from services.job_matcher import fingerprint_candidate
            result["fingerprint"] = fingerprint_candidate(
                result["name"], result["email"], result["phone"])
            from services.job_matcher import (generate_strengths, generate_weaknesses,
                                              generate_resume_summary)
            result["strengths"] = generate_strengths(result, result["required_skills"])
            result["weaknesses"] = generate_weaknesses(result, result["required_skills"])
            result["summary"] = generate_resume_summary(result)
        candidate_id = save_candidate(result)
        result["id"] = candidate_id
        # Persist the inputs needed to reproduce/recalculate this analysis.
        result["jd_meta"] = {k: v for k, v in jd_meta.items() if k != "clean_text"}
        result["job_description"] = job_description[:10000]
        from database.db_handler import update_candidate as _upd
        _upd(candidate_id, {"jd_meta": result["jd_meta"],
                            "job_description": result["job_description"],
                            "resume_file": os.path.basename(path)})
        analysis_id = _v1_store_analysis(candidate_id, {
            "candidate": {k: v for k, v in result.items() if k != "explainable"},
            "explainable": result.get("explainable", {}),
            "jd_meta": {k: v for k, v in jd_meta.items() if k != "clean_text"},
            "job_description": job_description[:10000],
            "required_experience": required_experience,
            "resume_file": os.path.basename(path),
        })
        return jsonify({"analysis_id": analysis_id, "candidate": result,
                        "report": (result.get("explainable") or {}).get("report")}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/v1/analyses/<analysis_id>", methods=["GET"])
def v1_get_analysis(analysis_id):
    if not _valid_analysis_id(analysis_id):
        return jsonify({"error": "Invalid analysis id."}), 400
    a = _get_stored_analysis(analysis_id)
    if not a:
        return jsonify({"error": "Analysis not found"}), 404
    return jsonify({"analysis_id": analysis_id, **a})


@app.route("/ready", methods=["GET"])
def readiness():
    """Readiness: config valid + store reachable. No model downloads."""
    checks = {}
    try:
        app_config.validate()
        checks["config"] = "ok"
    except Exception as e:
        checks["config"] = str(e)[:200]
    try:
        _store().ping()
        checks["store"] = "ok"
    except Exception as e:
        checks["store"] = str(e)[:200]
    from services import job_matcher as _jm
    checks["embed_model"] = _jm.embed_status() or "not-loaded-yet"
    ok = checks["config"] == "ok" and checks["store"] == "ok"
    return jsonify({"ready": ok, **checks}), (200 if ok else 503)


def _valid_analysis_id(analysis_id):
    import re
    # Charset-restricted (blocks traversal); unknown-but-wellformed ids map
    # to 404, malformed ones to 400.
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", analysis_id or ""))


@app.route("/api/v1/analyses/<analysis_id>", methods=["DELETE"])
@rate_limit(20)
def v1_delete_analysis(analysis_id):
    """Retention/deletion: remove the stored analysis and its resume file."""
    if not _valid_analysis_id(analysis_id):
        return jsonify({"error": "Invalid analysis id."}), 400
    ok, err = _review_authorized()
    if not ok:
        return err
    try:
        resume_file = _store().delete_analysis(analysis_id)
    except Exception as e:
        return _server_error(e, "v1_delete_analysis")
    if resume_file is None:
        return jsonify({"error": "Analysis not found"}), 404
    _ANALYSES.pop(analysis_id, None)
    if resume_file:
        try:
            # Basename only: stored names are generated, never user paths.
            os.remove(os.path.join(UPLOAD_FOLDER,
                                   os.path.basename(resume_file)))
        except Exception:
            pass
    try:
        from database.db_handler import delete_candidate
        delete_candidate(analysis_id)
    except Exception:
        pass
    return jsonify({"message": "Analysis deleted", "analysis_id": analysis_id})


@app.route("/api/v1/analyses/<analysis_id>/reviews", methods=["GET"])
def v1_list_reviews(analysis_id):
    """Append-only manual-review history for an analysis."""
    if not _valid_analysis_id(analysis_id):
        return jsonify({"error": "Invalid analysis id."}), 400
    try:
        events = _store().list_review_events(analysis_id)
    except Exception as e:
        return _server_error(e, "v1_list_reviews")
    return jsonify({"analysis_id": analysis_id, "events": events,
                    "count": len(events)})


@app.route("/api/v1/analyses/<analysis_id>/requirements", methods=["GET"])
def v1_get_requirements(analysis_id):
    if not _valid_analysis_id(analysis_id):
        return jsonify({"error": "Invalid analysis id."}), 400
    a = _get_stored_analysis(analysis_id)
    src = (a or {}).get("explainable") if a else None
    if not src:
        return jsonify({"error": "Analysis not found"}), 404
    return jsonify({"analysis_id": analysis_id,
                    "requirements": src.get("requirement_scores", []),
                    "aggregate": src.get("aggregate", {})})


@app.route("/api/v1/analyses/<analysis_id>/report", methods=["GET"])
def v1_get_report(analysis_id):
    if not _valid_analysis_id(analysis_id):
        return jsonify({"error": "Invalid analysis id."}), 400
    a = _get_stored_analysis(analysis_id)
    src = (a or {}).get("explainable") if a else None
    if not src:
        return jsonify({"error": "Analysis not found"}), 404
    report = src.get("report", {})
    fmt = (request.args.get("format") or "json").lower()
    if fmt == "pdf":
        # Renders the SAME stored report object, so the PDF cannot disagree
        # with the headline, the breakdown or the JSON export.
        from services.pdf_report import render_report_pdf
        pdf = render_report_pdf(report)
        resp = Response(pdf, mimetype="application/pdf")
        resp.headers["Content-Disposition"] = (
            f"attachment; filename=screening-report-{analysis_id}.pdf")
        return resp
    resp = jsonify(report)
    if fmt == "json":
        resp.headers["Content-Disposition"] = (
            f"attachment; filename=screening-report-{analysis_id}.json")
    return resp


@app.route("/api/v1/analyses/<analysis_id>/requirements", methods=["PATCH"])
@rate_limit(30)
def v1_review_requirements(analysis_id):
    """Manual review of extracted requirements, applied BEFORE final scoring.

    A reviewer can drop a requirement the parser invented, restore one it
    missed, or correct a weight. Every override is recorded on the requirement
    (reviewed_by / review_reason) AND as an append-only audit event, and the
    analysis is then re-scored from the same source documents.

    Body: {"exclude": ["asp.net"], "restore": ["kubernetes"],
           "weights": {"java": 4}, "mandatory": {"react": true},
           "expected_version": 3}
    """
    if not _valid_analysis_id(analysis_id):
        return jsonify({"error": "Invalid analysis id."}), 400
    ok, err = _review_authorized()
    if not ok:
        return err
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400
    err = _validate_review_body(body)
    if err:
        return jsonify({"error": err}), 400
    a = _get_stored_analysis(analysis_id)
    if not a:
        return jsonify({"error": "Analysis not found. Re-run the analysis "
                                 "to recreate it before reviewing."}), 404
    jd_text = a.get("job_description") or (a.get("jd_meta") or {}).get(
        "job_description", "")
    path = os.path.join(UPLOAD_FOLDER, a.get("resume_file", "") or "")
    if not a.get("resume_file") or not os.path.exists(path):
        return jsonify({"error": "The original resume is unavailable, so the "
                                 "analysis cannot be re-scored. Re-run it."}), 410

    reviewed_by = (body.get("reviewed_by") or "manual review")[:80]
    review_reason = (body.get("reason") or "reviewer correction")[:300]
    try:
        out = _rescore_with_overrides(a, path, jd_text, body, reviewed_by,
                                      review_reason,
                                      expected_version=body.get("expected_version"))
    except ValueError as e:
        msg = str(e)
        if "changed since you read it" in msg:
            return jsonify({"error": msg, "conflict": True,
                            "hint": "Re-read the analysis and retry with the "
                                    "current version."}), 409
        return jsonify({"error": msg}), 400
    except Exception as e:
        return _server_error(e, "v1_review_requirements")
    # Audit log: actor + actions only, never resume text or secrets.
    log.info("review analysis=%s actor=%s actions=%s version=%s",
             analysis_id, reviewed_by,
             [c.get("action") for c in out.get("changes", [])],
             out.get("version"))
    return jsonify(out)


def _validate_review_body(body):
    """Schema + value validation for manual-review requests."""
    import math
    allowed = {"exclude", "restore", "weights", "mandatory", "reviewed_by",
               "reason", "expected_version"}
    unknown = [k for k in body if k not in allowed]
    if unknown:
        return f"Unknown field(s) {unknown}; allowed: {sorted(allowed)}."
    for key in ("exclude", "restore"):
        v = body.get(key)
        if v is not None:
            if (not isinstance(v, list)
                    or not all(isinstance(x, str) and x.strip() for x in v)):
                return f"'{key}' must be a list of requirement ids."
            if len(v) > 50:
                return f"'{key}' lists too many requirements (max 50)."
    if body.get("exclude") and body.get("restore"):
        clash = set(x.strip().lower() for x in body["exclude"]) & set(
            x.strip().lower() for x in body["restore"])
        if clash:
            return (f"Invalid combination: {sorted(clash)} cannot be both "
                    f"excluded and restored in one request.")
    w = body.get("weights")
    if w is not None:
        if not isinstance(w, dict) or not w:
            return "'weights' must be a non-empty object {requirement: weight}."
        if len(w) > 100:
            return "'weights' has too many entries (max 100)."
        for k, v in w.items():
            if not isinstance(k, str) or not k.strip():
                return "'weights' keys must be requirement ids."
            try:
                fv = float(v)
            except (TypeError, ValueError):
                return f"weight for '{k}' must be a number, got {v!r}."
            if not math.isfinite(fv):
                return f"weight for '{k}' must be finite, got {v!r}."
            if not 1 <= fv <= 5:
                return f"weight for '{k}' must be in [1, 5], got {fv}."
    m = body.get("mandatory")
    if m is not None:
        if not isinstance(m, dict) or not m:
            return "'mandatory' must be a non-empty object {requirement: bool}."
        for k, v in m.items():
            if not isinstance(k, str) or not k.strip():
                return "'mandatory' keys must be requirement ids."
            if not isinstance(v, bool):
                return f"mandatory flag for '{k}' must be true/false."
    ev = body.get("expected_version")
    if ev is not None and (not isinstance(ev, int) or ev < 1):
        return "'expected_version' must be a positive integer."
    rb = body.get("reviewed_by")
    if rb is not None and not isinstance(rb, str):
        return "'reviewed_by' must be a string."
    rs = body.get("reason")
    if rs is not None and not isinstance(rs, str):
        return "'reason' must be a string."
    if not any(body.get(k) for k in ("exclude", "restore", "weights", "mandatory")):
        return "No review actions supplied (exclude/restore/weights/mandatory)."
    return None


def _rescore_with_overrides(stored, resume_path, jd_text, body, reviewed_by,
                           review_reason, expected_version=None):
    """Re-run the pipeline with reviewer corrections applied to requirements."""
    from services.requirement_extractor import extract_requirements
    from services.evidence_extractor import extract_evidence
    from services.scoring_engine import analyze as engine_analyze
    from services.eligibility_checker import check_eligibility
    from services.report_generator import build_report, analyze_gaps
    from services.skill_extractor import (
        extract_skills, extract_skills_with_categories,
        extract_experience_years, extract_education)
    from services.resume_parser import parse_resume

    requirements = extract_requirements(jd_text, stored.get("jd_meta") or {})
    if not requirements:
        raise ValueError("insufficient-data: no scoreable requirements")

    by_canon = {r["canonical_skill"]: r for r in requirements}
    changes = []

    for canon in body.get("exclude") or []:
        canon = (canon or "").strip().lower()
        if canon in by_canon:
            by_canon[canon]["excluded"] = True
            by_canon[canon]["review_note"] = f"excluded by {reviewed_by}"
            changes.append({"requirement": canon, "action": "excluded"})
    for canon in body.get("restore") or []:
        canon = (canon or "").strip().lower()
        hit = next((r for r in requirements if r["canonical_skill"] == canon), None)
        if hit:
            hit["excluded"] = False
            hit["review_note"] = f"restored by {reviewed_by}"
            changes.append({"requirement": canon, "action": "restored"})
        else:
            raise ValueError(f"cannot restore '{canon}': not a requirement "
                             f"extracted from this JD")
    for canon, weight in (body.get("weights") or {}).items():
        canon = (canon or "").strip().lower()
        if canon not in by_canon:
            raise ValueError(f"unknown requirement '{canon}'")
        import math as _math
        try:
            w = float(weight)
        except (TypeError, ValueError):
            raise ValueError(f"weight for '{canon}' must be a number, "
                             f"got {weight!r}")
        if not _math.isfinite(w):
            raise ValueError(f"weight for '{canon}' must be finite, got {weight!r}")
        if not 1 <= w <= 5:
            raise ValueError(f"weight for '{canon}' must be in [1, 5], got {w}")
        prev = by_canon[canon].get("importance_weight")
        by_canon[canon]["importance_weight"] = w
        by_canon[canon]["weight_source"] = "reviewed"
        by_canon[canon]["weight_reason"] = f"set by {reviewed_by}: {review_reason}"
        changes.append({"requirement": canon, "action": "reweight", "value": w,
                        "previous": prev})
    for canon, flag in (body.get("mandatory") or {}).items():
        canon = (canon or "").strip().lower()
        if canon not in by_canon:
            raise ValueError(f"unknown requirement '{canon}'")
        by_canon[canon]["mandatory"] = bool(flag)
        by_canon[canon]["mandatory_explicit"] = False
        by_canon[canon]["mandatory_source"] = (
            f"set by {reviewed_by} (reviewed, not JD-explicit)")
        changes.append({"requirement": canon, "action": "mandatory",
                        "value": bool(flag)})

    for r in requirements:
        r.setdefault("excluded", False)
        r["reviewed_by"] = reviewed_by
        r["review_reason"] = review_reason
    scored_requirements = [r for r in requirements if not r["excluded"]]

    parsed = parse_resume(resume_path)
    skills = extract_skills(parsed["clean_text"])
    exp_years, exp_detail = extract_experience_years(parsed["raw_text"],
                                                     detail=True)
    education = extract_education(parsed["raw_text"])
    want = sorted({r["canonical_skill"] for r in scored_requirements} | set(skills))
    evidence = extract_evidence(parsed, want)

    analysis = engine_analyze(
        scored_requirements, skills, evidence, parsed.get("clean_text", ""),
        exp_years, stored.get("required_experience") or 0,
        experience_profile=exp_detail)
    if analysis.get("status") != "ok":
        raise ValueError(analysis.get("reason", "insufficient-data"))

    eligibility = check_eligibility(scored_requirements, evidence, education,
                                    exp_years, stored.get("required_experience") or 0,
                                    stored.get("jd_meta") or {}, jd_text=jd_text)
    gaps = analyze_gaps(analysis["requirements"])
    report = build_report(
        {"name": parsed.get("name"), "education": education,
         "experience_years": exp_years},
        scored_requirements, analysis["requirements"], analysis["aggregate"],
        eligibility, evidence, stored.get("jd_meta") or {}, gaps,
        extraction_status=parsed.get("extraction_status", "ok"))

    analysis_id = stored.get("id") or stored.get("candidate_id") or ""
    explainable = {"status": "ok", "requirements": analysis["requirements"],
                   "requirement_scores": analysis["requirements"],
                   "aggregate": analysis["aggregate"],
                   "eligibility": eligibility, "gaps": gaps, "report": report,
                   "config_version": analysis.get("config_version"),
                   "formula_version": analysis.get("formula_version")}
    # Persist scores + audit events in the store (authoritative). The
    # store bumps `version`; a concurrent review holding a stale
    # expected_version gets 409 instead of silently overwriting.
    from database.store import AnalysisNotFound, VersionConflict
    _ACTION_MAP = {"excluded": "exclude", "restored": "restore",
                   "weight": "reweight", "reweight": "reweight",
                   "mandatory": "mandatory"}
    try:
        new_version = _store().update_scores(
            analysis_id, explainable,
            candidate_patch={"final_score": analysis["aggregate"]["overall_score"],
                             "experience_years": exp_years, "education": education},
            expected_version=expected_version)
        _store().append_review_events(analysis_id, [{
            "actor": reviewed_by, "action": _ACTION_MAP.get(c["action"], "reweight"),
            "requirement_id": c.get("requirement", ""),
            "previous_value": c.get("previous"),
            "updated_value": c.get("value"),
            "reason": review_reason} for c in changes])
    except VersionConflict as e:
        raise ValueError(str(e))
    except AnalysisNotFound:
        raise ValueError("Analysis not found in the persistent store.")
    payload = _store().get_analysis(analysis_id) or {}
    _ANALYSES[analysis_id] = {
        "id": analysis_id,
        "candidate": payload.get("candidate") or {
            "final_score": analysis["aggregate"]["overall_score"],
            "experience_years": exp_years, "education": education},
        "explainable": explainable, "jd_meta": stored.get("jd_meta") or {},
        "job_description": jd_text,
        "required_experience": stored.get("required_experience") or 0,
        "resume_file": stored.get("resume_file"),
        "version": new_version,
    }
    return {
        "analysis_id": analysis_id,
        "version": new_version,
        "reviewed_by": reviewed_by,
        "review_reason": review_reason,
        "changes": changes,
        "excluded": [r["canonical_skill"] for r in requirements if r["excluded"]],
        "requirements": analysis["requirements"],
        "aggregate": analysis["aggregate"],
        "eligibility": eligibility,
        "report": report,
    }


@app.route("/api/v1/analyses/<analysis_id>/recalculate", methods=["POST"])
@rate_limit(30)
def v1_recalculate(analysis_id):
    """Re-run the same documents with different overall weights.

    Reads the authoritative stored record (never a live session), re-scores,
    persists the new scores, and returns them. The stored report is rebuilt
    from the same result, so JSON/PDF/API agree.
    """
    if not _valid_analysis_id(analysis_id):
        return jsonify({"error": "Invalid analysis id."}), 400
    try:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400
        weights_override = _v1_validate_weights(body)
        if not weights_override:
            return jsonify({"error": "Provide weights {keyword, semantic, experience} summing to 1.0"}), 400
        a = _get_stored_analysis(analysis_id)
        if not a:
            return jsonify({
                "error": "Analysis not found. Recalculation needs the stored "
                         "resume, so re-run the analysis to recreate it.",
                "recoverable": "re-run analysis",
            }), 404
        path = os.path.join(UPLOAD_FOLDER, a.get("resume_file", ""))
        if not a.get("resume_file") or not os.path.exists(path):
            return jsonify({
                "error": "The original resume document for this analysis is no "
                         "longer available, so it cannot be recalculated. "
                         "Re-run the analysis to reproduce it with new weights.",
                "recoverable": "re-run analysis",
            }), 410
        from services.requirement_extractor import extract_requirements
        from services.evidence_extractor import extract_evidence
        from services.scoring_engine import analyze as engine_analyze
        from services.report_generator import build_report, analyze_gaps
        from services.eligibility_checker import check_eligibility
        extd = _extract_resume(path)
        jd_meta = a.get("jd_meta", {})
        requirements = extract_requirements(a.get("job_description", ""), jd_meta)
        evidence = extract_evidence(extd["parsed"], extd["skills_found"])
        analysis = engine_analyze(requirements, extd["skills_found"], evidence,
                                  extd["parsed"].get("clean_text", ""),
                                  extd["experience_years"], a.get("required_experience", 0),
                                  weights_override=weights_override)
        if analysis.get("status") != "ok":
            return jsonify({"error": analysis.get("reason")}), 422
        gaps = analyze_gaps(analysis["requirements"])
        eligibility = check_eligibility(requirements, evidence, extd["education"],
                                        extd["experience_years"],
                                        a.get("required_experience", 0), jd_meta)
        report = build_report({"name": extd["parsed"].get("name"),
                               "education": extd["education"],
                               "experience_years": extd["experience_years"]},
                              requirements, analysis["requirements"],
                              analysis["aggregate"], eligibility, evidence,
                              jd_meta, gaps)
        explainable = {"status": "ok",
                       "requirement_scores": analysis["requirements"],
                       "requirements": analysis["requirements"],
                       "aggregate": analysis["aggregate"],
                       "eligibility": eligibility, "gaps": gaps, "report": report,
                       "config_version": analysis.get("config_version"),
                       "formula_version": analysis.get("formula_version")}
        try:
            new_version = _store().update_scores(analysis_id, explainable)
        except Exception as e:
            log.error("recalculate persist failed: %s", type(e).__name__)
            if _is_prod():
                return jsonify({"error": "Recalculation succeeded but could not "
                                         "be persisted. Retry shortly."}), 503
        else:
            _ANALYSES.pop(analysis_id, None)  # drop stale cache; store is truth
        a["explainable"] = explainable
        return jsonify({"analysis_id": analysis_id, "aggregate": analysis["aggregate"],
                        "requirements": analysis["requirements"], "report": report})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return _server_error(e, "v1_recalculate")


# ── Analyze a single resume ───────────────────────────────────
@app.route("/analyze", methods=["POST"])
def analyze_resume():
    too_big = _check_request_size()
    if too_big:
        return too_big
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
        try:
            _sniff_upload(file_path, file_ext)
        except ValueError as ve:
            try:
                os.remove(file_path)
            except Exception:
                pass
            return jsonify({"error": str(ve)}), 400

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
    too_big = _check_request_size()
    if too_big:
        return too_big
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
                try:
                    _sniff_upload(file_path, file_ext)
                except ValueError as ve:
                    os.remove(file_path)
                    errors.append({"file": file.filename, "error": str(ve)})
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
                                          job_description, skill_weights, idf, must_have,
                                          jd_meta)

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
    if os.getenv("APP_ENV") == "production" or os.getenv("FLASK_ENV") == "production":
        return jsonify({"error": "Clear is disabled in production"}), 403
    try:
        from database.db_handler import clear_all_candidates
        clear_all_candidates()
        return jsonify({"message": "All candidates cleared"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO)
    try:
        app_config.validate()
    except RuntimeError as e:
        print(f"Configuration error:\n{e}\nRefusing to start.")
        raise SystemExit(1)
    try:
        _store().ping()
    except Exception as e:
        print(f"{e}\nRefusing to start without the analysis database.")
        raise SystemExit(1)
    print(f"Config: {app_config.safe_summary()}")
    port = int(os.getenv("PORT", 5000))
    is_dev = os.getenv("APP_ENV") != "production" and os.getenv("FLASK_ENV") != "production"
    print(f"\n>> AI Resume Analyzer API running on http://localhost:{port} (debug={is_dev})\n")
    app.run(host="0.0.0.0", debug=is_dev, port=port)
