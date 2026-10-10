"""Precision matching engine: weighted skills + chunked semantics + fit signals."""

import hashlib
import math
import os
import re

try:
    from services.skill_aliases import normalize_skill_list
except ImportError:
    from skill_aliases import normalize_skill_list

# ── Light TF-IDF embedder (no torch; for low-RAM hosts) ──
class _TfidfEmbedder:
    """Drop-in replacement for SentenceTransformer.encode.

    Fits its vocabulary/IDF once (taxonomy seed + first texts seen),
    then freezes it so separately-encoded JD and resume matrices stay
    in the same vector space. OOV terms after the freeze are ignored.
    """
    NAME = "tfidf-cosine (light)"

    def __init__(self):
        self.vocab = {}
        self.idf = None
        self._fitted = False

    @staticmethod
    def _tokens(t):
        return re.findall(r"[a-z0-9]+(?:[+#])?", (t or "").lower())

    def _fit(self, texts):
        from collections import Counter
        seed = list(texts)
        try:
            try:
                from services.skill_extractor import all_skills as _tax
            except ImportError:
                from skill_extractor import all_skills as _tax
            seed = list(_tax) + seed
        except Exception:
            pass
        df = Counter()
        for t in seed:
            for tok in set(self._tokens(t)):
                df[tok] += 1
        if not df:
            df = Counter({"resume": 1, "job": 1})
        ordered = sorted(df)
        import numpy as _np
        n = max(len(seed), 1)
        self.vocab = {tok: i for i, tok in enumerate(ordered)}
        self.idf = _np.array(
            [math.log((n + 1) / (df[tok] + 1)) + 1.0 for tok in ordered],
            dtype=_np.float32)
        self._fitted = True

    def encode(self, texts, normalize_embeddings=True,
               convert_to_numpy=True, show_progress_bar=False):
        import numpy as _np
        if isinstance(texts, str):
            texts = [texts]
        texts = list(texts)
        if not self._fitted:
            self._fit(texts)
        mat = _np.zeros((len(texts), len(self.vocab)), dtype=_np.float32)
        for i, t in enumerate(texts):
            for tok in self._tokens(t):
                j = self.vocab.get(tok)
                if j is not None:
                    mat[i, j] += 1.0
        if mat.shape[1]:
            mat *= self.idf
        if normalize_embeddings:
            n = _np.linalg.norm(mat, axis=1, keepdims=True)
            n[n == 0] = 1.0
            mat /= n
        return mat


# ── Embedding model cascade (best first, cached once) ──
# BAAI/bge-small-en-v1.5: same size class as MiniLM (~33M params) but
# measurably stronger at retrieval-style semantic similarity.
# SEMANTIC_ENGINE=tfidf (or no torch stack) selects the light engine.
MODEL_CANDIDATES = ('BAAI/bge-small-en-v1.5', 'all-MiniLM-L6-v2')
_EMBED_MODEL = None
_EMBED_MODEL_NAME = None


def _light_engine(reason):
    global _EMBED_MODEL, _EMBED_MODEL_NAME
    _EMBED_MODEL = _TfidfEmbedder()
    _EMBED_MODEL_NAME = _TfidfEmbedder.NAME
    print(f"Embedding engine: TF-IDF cosine ({reason})")
    return _EMBED_MODEL


def _get_embed_model():
    global _EMBED_MODEL, _EMBED_MODEL_NAME
    if _EMBED_MODEL is not None:
        return _EMBED_MODEL
    if os.environ.get("SEMANTIC_ENGINE", "").lower() == "tfidf":
        return _light_engine("SEMANTIC_ENGINE=tfidf")
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as e:
        return _light_engine(f"no torch stack ({e})")
    last_err = None
    for name in MODEL_CANDIDATES:
        try:
            _EMBED_MODEL = SentenceTransformer(name)
            _EMBED_MODEL_NAME = name
            print(f"Embedding model loaded: {name}")
            return _EMBED_MODEL
        except Exception as e:
            last_err = e
            print(f"Embedding model {name} unavailable ({e}), trying fallback...")
    raise RuntimeError(f"No embedding model available: {last_err}")


def get_embed_model_metadata():
    """Provenance for reproducibility: engine kind, model id and revision.

    Spec F.1/F.2 require the exact embedding model and version behind every
    semantic number. `sha` is the model identifier (a HuggingFace repo id or a
    fixed tag for the light engine) plus, where available, the library
    version, so a result can be tied to what produced it.
    """
    name = _EMBED_MODEL_NAME
    if not name:
        return {"engine": None, "model": None, "version": None, "dimension": None}
    version = None
    try:
        import sentence_transformers as _st
        version = getattr(_st, "__version__", None)
    except Exception:
        version = None
    dim = None
    try:
        dim = int(_get_embed_model().encode(["dimension probe"]).shape[1])
    except Exception:
        pass
    return {"engine": ("tfidf" if name == _TfidfEmbedder.NAME else "sentence-transformers"),
            "model": name, "version": version, "dimension": dim}


def get_embed_model_name():
    try:
        _get_embed_model()
    except Exception:
        return None
    return _EMBED_MODEL_NAME


def embed_status():
    """Cached-only embedding state (never triggers a download).

    Returns the loaded model name, or None if no model is loaded yet
    (e.g. first call hasn't happened, or all downloads failed).
    """
    return _EMBED_MODEL_NAME


# ── Embedding cache (chunk text hash -> normalized vector) ──
_EMBED_CACHE = {}
_EMBED_CACHE_MAX = 4000


def _embed_batch(texts):
    """Encode texts to L2-normalized numpy vectors, using the cache."""
    import numpy as np
    vecs = [None] * len(texts)
    missing, missing_idx = [], []
    for i, t in enumerate(texts):
        h = hashlib.sha1(t.encode("utf-8", "ignore")).hexdigest()
        v = _EMBED_CACHE.get(h)
        if v is not None:
            vecs[i] = v
        else:
            missing.append(t)
            missing_idx.append((i, h))
    if missing:
        model = _get_embed_model()
        enc = model.encode(missing, normalize_embeddings=True,
                           convert_to_numpy=True, show_progress_bar=False)
        for (i, h), v in zip(missing_idx, enc):
            v = np.asarray(v, dtype=np.float32)
            vecs[i] = v
            if len(_EMBED_CACHE) >= _EMBED_CACHE_MAX:
                _EMBED_CACHE.clear()
            _EMBED_CACHE[h] = v
    import numpy as _np
    return _np.stack(vecs)


EDUCATION_RANK = {
    "Not specified": 0,
    "Diploma/Certificate": 1,
    "Bachelors": 2,
    "Masters": 3,
    "PhD": 4,
}


def _norm_sets(resume_skills, required_skills):
    resume_set = set(normalize_skill_list(resume_skills))
    required_set = set(normalize_skill_list([s for s in required_skills if s and s.strip()]))
    return resume_set, required_set


def calculate_match_score(resume_skills, required_skills):
    """
    Keyword-based match score (alias-aware).
    Score = (matched skills / required skills) * 100
    """
    resume_set, required_set = _norm_sets(resume_skills, required_skills)
    if not required_set:
        return 0.0
    matched = resume_set.intersection(required_set)
    score = (len(matched) / len(required_set)) * 100
    return round(score, 1)


def get_matched_skills(resume_skills, required_skills):
    """Return list of skills that matched (alias-aware)."""
    resume_set, required_set = _norm_sets(resume_skills, required_skills)
    return sorted(list(resume_set.intersection(required_set)))


def get_missing_skills(resume_skills, required_skills):
    """Return list of required skills missing from resume (alias-aware)."""
    resume_set, required_set = _norm_sets(resume_skills, required_skills)
    return sorted(list(required_set - resume_set))


def compute_idf(pool_skill_sets):
    """IDF over an applicant pool: rare-among-applicants skills weigh more.

    idf(s) = log((1 + N) / (1 + df(s))) + 1  → common skills ≈ 1.0.
    pool_skill_sets: iterable of normalized skill sets/lists.
    """
    df = {}
    pool = [set(s) for s in (pool_skill_sets or [])]
    n = len(pool) or 1
    for s in pool:
        for skill in s:
            df[skill] = df.get(skill, 0) + 1
    return {skill: round(math.log((1 + n) / (1 + c)) + 1.0, 3)
            for skill, c in df.items()}


def _fuzzy_best(target, candidates, threshold=0.85):
    """Best difflib ratio of target vs candidates (single-word aware)."""
    import difflib
    best, best_c = 0.0, None
    for c in candidates:
        r = difflib.SequenceMatcher(None, target, c).ratio()
        if r > best:
            best, best_c = r, c
    if best >= threshold:
        return best_c, round(best, 3)
    return None, 0.0


def _fuzzy_in_text(target, text, threshold=0.80):
    """Scan raw resume tokens for a near-miss (typo/variant) of a skill.

    Skill-level fuzzy matching can never fire for typos because the
    extractor itself misses misspelled skills — so there is no candidate
    to compare against. This falls back to the raw text tokens
    (n-grams for multi-word skills). Noisier signal: use a lower
    threshold and partial credit at the call site.
    """
    import difflib
    import re
    if not target or len(target) < 2 or not text:
        return None, 0.0
    tokens = [t for t in re.findall(r"[a-z0-9+#]+(?:\.[a-z0-9+#]+)*", text.lower())
              if len(t) >= 2]
    if not tokens:
        return None, 0.0
    n = len(target.split())
    if n == 1:
        cands = tokens
    else:
        cands = [" ".join(tokens[i:i + n])
                 for i in range(len(tokens) - n + 1)]
    best, best_c = 0.0, None
    for c in cands:
        if c == target:
            continue  # exact hits are handled upstream
        r = difflib.SequenceMatcher(None, target, c).ratio()
        if r > best:
            best, best_c = r, c
    if best >= threshold:
        return best_c, round(best, 3)
    return None, 0.0


def calculate_weighted_skill_score(resume_skills, skill_weights,
                                   idf=None, fuzzy_threshold=0.85,
                                   fuzzy_credit=0.7, resume_text=None,
                                   text_fuzzy_threshold=0.80,
                                   text_fuzzy_credit=0.6):
    """Precision skill coverage.

    weight(s) = JD_weight(s) × IDF(s); exact match = full credit,
    skill-level fuzzy match = fuzzy_credit, raw-text typo hit =
    text_fuzzy_credit, else 0.
    Returns dict(score, matched, missing, fuzzy_matches, coverage).
    """
    resume_set = set(normalize_skill_list(resume_skills))
    weights = {normalize_skill_list([k])[0]: float(v)
               for k, v in (skill_weights or {}).items() if k}
    if not weights:
        return {"score": 0.0, "matched": [], "missing": [],
                "fuzzy_matches": {}, "coverage": 0.0}
    idf = idf or {}
    matched, missing, fuzzy_hits = [], [], {}
    total = 0.0
    credit = 0.0
    for req, jd_w in weights.items():
        w = jd_w * idf.get(req, 1.0)
        total += w
        if req in resume_set:
            credit += w
            matched.append(req)
        else:
            hit, ratio = _fuzzy_best(req, resume_set, fuzzy_threshold)
            if hit:
                credit += w * fuzzy_credit
                matched.append(req)
                fuzzy_hits[req] = {"found_as": hit, "ratio": ratio}
                continue
            if resume_text:
                thit, tratio = _fuzzy_in_text(req, resume_text,
                                              text_fuzzy_threshold)
                if thit:
                    credit += w * text_fuzzy_credit
                    matched.append(req)
                    fuzzy_hits[req] = {"found_as": thit, "ratio": tratio,
                                       "via": "raw_text"}
                    continue
            missing.append(req)
    score = round((credit / total) * 100, 1) if total else 0.0
    return {"score": score, "matched": sorted(matched),
            "missing": sorted(missing), "fuzzy_matches": fuzzy_hits,
            "coverage": round((len(matched) / len(weights)) * 100, 1)}


def chunk_text(text, chunk_words=120, overlap_words=30, max_chunks=48):
    """Split long text into overlapping word chunks for MaxSim scoring."""
    words = (text or "").split()
    if not words:
        return []
    chunks = []
    step = max(1, chunk_words - overlap_words)
    for start in range(0, len(words), step):
        piece = " ".join(words[start:start + chunk_words])
        if piece.strip():
            chunks.append(piece)
        if len(chunks) >= max_chunks:
            break
    return chunks


# ── Per-JD artifacts (computed once per unique JD, reused across resumes) ──
_JD_CACHE = {}


def _get_jd_artifacts(jd_text, max_jd_chunks=16):
    """Return (jd_chunk_matrix, jd_skills) for a JD, cached by hash."""
    key = hashlib.sha1((jd_text or "").encode("utf-8", "ignore")).hexdigest()
    hit = _JD_CACHE.get(key)
    if hit is not None:
        return hit
    chunks = chunk_text(jd_text or "", max_chunks=64)
    try:
        from services.skill_extractor import extract_required_skills_from_jd
    except ImportError:
        try:
            from skill_extractor import extract_required_skills_from_jd
        except ImportError:
            extract_required_skills_from_jd = None
    skills = []
    if extract_required_skills_from_jd and jd_text:
        try:
            skills = extract_required_skills_from_jd(jd_text, max_skills=30)
        except Exception:
            skills = []
    # Keep the most skill-dense JD chunks (requirements carry the signal).
    def _density(c):
        low = c.lower()
        return sum(1 for s in skills if s in low)
    ranked = sorted(chunks, key=_density, reverse=True)[:max_jd_chunks] or chunks[:max_jd_chunks]
    # Stable order: densest first is fine for MaxSim mean.
    matrix = _embed_batch(ranked) if ranked else None
    _JD_CACHE[key] = (matrix, skills)
    if len(_JD_CACHE) > 64:
        _JD_CACHE.pop(next(iter(_JD_CACHE)))
    return _JD_CACHE[key]


def calculate_semantic_score(resume_text, job_description):
    """
    Chunked MaxSim semantic similarity (70%) + global cosine (30%).

    Each JD chunk takes its best-matching resume chunk, averaged —
    so a resume covering every requirement beats one matching a
    single paragraph. Full documents are used, not a 2000-char cut.
    Falls back to 0 if no model/JD available.
    """
    if not resume_text or not job_description:
        return 0.0
    try:
        jd_matrix, _ = _get_jd_artifacts(job_description)
        if jd_matrix is None:
            return 0.0
        r_chunks = chunk_text(resume_text, max_chunks=48)
        if not r_chunks:
            return 0.0
        r_matrix = _embed_batch(r_chunks)
        sims = r_matrix @ jd_matrix.T  # cosine (vectors normalized)
        maxsim = float(sims.max(axis=0).mean())
        # Global fit signal on head text
        model = _get_embed_model()
        g = model.encode([resume_text[:4000], job_description[:4000]],
                         normalize_embeddings=True, convert_to_numpy=True,
                         show_progress_bar=False)
        import numpy as _np
        glob = float(_np.dot(g[0], g[1]))
        blended = 0.7 * maxsim + 0.3 * glob
        return round(max(0.0, blended) * 100, 1)
    except Exception as e:
        print(f"Semantic scoring unavailable: {e}")
        return 0.0


def calculate_education_score(education, prefer_masters_plus=False):
    """Score education 0-100. Higher degrees score higher."""
    rank = EDUCATION_RANK.get(education, 0)
    if prefer_masters_plus:
        return {0: 40.0, 1: 55.0, 2: 75.0, 3: 100.0, 4: 100.0}.get(rank, 50.0)
    return {0: 50.0, 1: 65.0, 2: 80.0, 3: 95.0, 4: 100.0}.get(rank, 50.0)


def calculate_experience_score(actual_years, required_years):
    """0-100 experience score with partial credit + small over-experience cap."""
    if required_years <= 0:
        return 100.0 if actual_years > 0 else 60.0
    if actual_years >= required_years:
        return 100.0
    if actual_years <= 0:
        return 0.0
    return round((actual_years / required_years) * 100, 1)


DEFAULT_WEIGHTS = {"keyword": 0.45, "semantic": 0.30,
                   "experience": 0.15, "education": 0.10}
MUST_HAVE_PENALTY_EACH = 6.0
MUST_HAVE_PENALTY_CAP = 24.0


def calculate_final_score(keyword_score, semantic_score, experience_years,
                           required_experience=0, education="Not specified",
                           weights=None, must_have_missing=0):
    """
    Precision final score.
    Default weights: keyword 45% / semantic 30% / experience 15% / education 10%.
    If semantic_score == 0 (no JD / no model), its weight redistributes
    to keyword. Missing must-have skills apply a gate penalty
    (6 pts each, capped at 24) so resumes lacking core requirements
    cannot top the ranking on nice-to-haves alone.
    """
    exp_score = calculate_experience_score(experience_years, required_experience)
    edu_score = calculate_education_score(education)

    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)

    if semantic_score == 0:
        # redistribute semantic weight to keyword
        w["keyword"] += w["semantic"]
        w["semantic"] = 0.0

    total = sum(w.values()) or 1.0
    final = (keyword_score * w["keyword"] + semantic_score * w["semantic"]
             + exp_score * w["experience"] + edu_score * w["education"]) / total
    penalty = min(MUST_HAVE_PENALTY_CAP, MUST_HAVE_PENALTY_EACH * (must_have_missing or 0))
    final = max(0.0, min(final, 100.0) - penalty)
    return round(final, 1)


def explain_score(keyword_score, semantic_score, experience_years,
                   required_experience=0, education="Not specified",
                   must_have_missing=0):
    """Return transparent per-signal breakdown for UI explainability."""
    exp_score = calculate_experience_score(experience_years, required_experience)
    edu_score = calculate_education_score(education)
    if semantic_score == 0:
        weights = {"keyword": 0.75, "semantic": 0.0, "experience": 0.15, "education": 0.10}
    else:
        weights = dict(DEFAULT_WEIGHTS)
    return {
        "keyword_score": keyword_score,
        "semantic_score": semantic_score,
        "experience_score": exp_score,
        "education_score": edu_score,
        "must_have_missing": must_have_missing,
        "must_have_penalty": round(min(MUST_HAVE_PENALTY_CAP,
                                       MUST_HAVE_PENALTY_EACH * (must_have_missing or 0)), 1),
        "weights": weights,
        "embed_model": _EMBED_MODEL_NAME,
    }


def fingerprint_candidate(name, email, phone):
    """Stable dedupe key: prefer email, else phone, else name."""
    import hashlib
    if (name or "").strip().lower() in ("unknown candidate", "unknown"):
        name = None  # placeholder is identical for every parsed-failed resume
    base = (email or phone or name or "").strip().lower()
    if not base:
        return None
    return hashlib.md5(base.encode()).hexdigest()[:12]


def generate_recommendation(score):
    """Generate a human-readable recommendation based on score."""
    if score >= 80:
        return {"label": "Strong Match", "color": "green",
                "message": "Highly recommended for interview."}
    elif score >= 60:
        return {"label": "Good Match", "color": "blue",
                "message": "Recommended — meets most requirements."}
    elif score >= 40:
        return {"label": "Partial Match", "color": "orange",
                "message": "Consider for review — some skills missing."}
    else:
        return {"label": "Weak Match", "color": "red",
                "message": "Does not meet core requirements."}


# ── New functions for ATS decision-support ─────────────────────

def calculate_skills_match_pct(matched_skills, required_skills):
    """Return 0-100 percentage of required skills matched (alias-aware)."""
    _, required_set = _norm_sets([], required_skills)
    if not required_set:
        return 0
    matched_set = set(normalize_skill_list(matched_skills))
    return round((len(matched_set.intersection(required_set)) / len(required_set)) * 100, 1)


def calculate_experience_match_pct(actual_years, required_years):
    """Return 0-100 percentage of experience match."""
    if required_years <= 0:
        return 100.0 if actual_years > 0 else 50.0
    if actual_years >= required_years:
        return 100.0
    return round((actual_years / required_years) * 100, 1)


def generate_strengths(candidate_data, required_skills=None):
    """
    Identify strength areas from candidate data.
    Returns list of strength strings (max 3).
    """
    strengths = []
    skills_found = candidate_data.get("skills_found", [])
    matched = candidate_data.get("matched_skills", [])
    exp = candidate_data.get("experience_years", 0)
    score = candidate_data.get("final_score", 0)
    education = candidate_data.get("education", "")

    # Strength: High skill match
    if required_skills:
        required_set = set(s.lower().strip() for s in required_skills if s.strip())
        if required_set:
            match_ratio = len(matched) / len(required_set)
            if match_ratio >= 0.8:
                strengths.append(f"Covers {len(matched)}/{len(required_set)} required skills")
            elif match_ratio >= 0.5:
                strengths.append(f"Matches {len(matched)} of {len(required_set)} key skills")

    # Strength: Broad skillset
    if len(skills_found) >= 10:
        strengths.append(f"Broad skillset with {len(skills_found)} technologies")
    elif len(skills_found) >= 5:
        strengths.append(f"Solid tech stack ({len(skills_found)} skills detected)")

    # Strength: Experience
    if exp >= 8:
        strengths.append(f"Senior-level experience ({exp} years)")
    elif exp >= 4:
        strengths.append(f"Mid-level experience ({exp} years)")
    elif exp >= 2:
        strengths.append(f"Relevant experience ({exp} years)")

    # Strength: Education
    if education == "PhD":
        strengths.append("Advanced research background (PhD)")
    elif education == "Masters":
        strengths.append("Strong academic foundation (Masters)")

    # Strength: High overall score
    if score >= 80:
        strengths.append("Strong overall match for the role")

    return strengths[:3] if strengths else ["No standout strengths detected for this role"]


def generate_weaknesses(candidate_data, required_skills=None):
    """
    Identify weakness areas from candidate data.
    Returns list of weakness strings (max 3).
    """
    weaknesses = []
    missing = candidate_data.get("missing_skills", [])
    exp = candidate_data.get("experience_years", 0)
    score = candidate_data.get("final_score", 0)
    skills_found = candidate_data.get("skills_found", [])
    education = candidate_data.get("education", "Not specified")

    # Weakness: Missing skills
    if missing:
        if len(missing) >= 4:
            weaknesses.append(f"Missing {len(missing)} required skills")
        elif len(missing) >= 2:
            top_missing = ", ".join(missing[:3])
            weaknesses.append(f"Missing: {top_missing}")
        else:
            weaknesses.append(f"Missing: {missing[0]}")

    # Weakness: Limited experience
    req_exp = candidate_data.get("required_experience", 0)
    if req_exp > 0 and exp < req_exp:
        gap = req_exp - exp
        weaknesses.append(f"Experience gap: {gap}y below requirement")
    elif exp == 0:
        weaknesses.append("No experience years detected")

    # Weakness: Narrow skillset
    if len(skills_found) < 3:
        weaknesses.append("Limited skills detected in resume")

    # Weakness: Education
    if education == "Not specified":
        weaknesses.append("Education level not specified")

    # Weakness: Low score
    if score < 40:
        weaknesses.append("Below threshold for role requirements")

    return weaknesses[:3] if weaknesses else ["No major gaps identified"]


def generate_resume_summary(candidate_data):
    """
    Generate a 2-3 sentence summary of the candidate based on extracted data.
    """
    name = candidate_data.get("name", "This candidate")
    exp = candidate_data.get("experience_years", 0)
    education = candidate_data.get("education", "unspecified education")
    skills_found = candidate_data.get("skills_found", [])
    matched = candidate_data.get("matched_skills", [])
    missing = candidate_data.get("missing_skills", [])
    score = candidate_data.get("final_score", 0)
    recommendation = candidate_data.get("recommendation", {})

    # Build summary
    parts = []

    # Sentence 1: Background
    has_edu = education and education != "Not specified"
    if exp > 0:
        edu_txt = f"with a {education} degree" if has_edu else "with no education level detected"
        parts.append(f"{name} has {exp} years of experience {edu_txt}.")
    elif has_edu:
        parts.append(f"{name} holds a {education} degree.")
    else:
        parts.append(f"{name}'s resume shows no detectable education or experience details.")

    # Sentence 2: Skills
    if skills_found:
        top_skills = ", ".join(skills_found[:5])
        parts.append(f"Key competencies include {top_skills}.")

    # Sentence 3: Fit assessment
    if score >= 80:
        parts.append(f"With a {score}% match score, this candidate is a strong fit for the role.")
    elif score >= 60:
        parts.append(f"At {score}% match, the candidate meets most requirements but has some gaps.")
    elif score >= 40:
        if missing:
            parts.append(f"Scored {score}% — missing {len(missing)} required skill(s) which may need development.")
        else:
            parts.append(f"Scored {score}% — partial alignment with role requirements.")
    else:
        parts.append(f"At {score}% match, significant gaps exist between the candidate's profile and job requirements.")

    return " ".join(parts)


def compare_candidates(c1, c2):
    """
    Compare two candidates side-by-side.
    Returns structured comparison with recommendation.
    """
    comparison = {
        "candidate_1": {
            "id": c1.get("id"),
            "name": c1.get("name", "Candidate 1"),
            "final_score": c1.get("final_score", 0),
            "keyword_score": c1.get("keyword_score", 0),
            "experience_years": c1.get("experience_years", 0),
            "education": c1.get("education", "—"),
            "skills_found": c1.get("skills_found", []),
            "matched_skills": c1.get("matched_skills", []),
            "missing_skills": c1.get("missing_skills", []),
            "strengths": c1.get("strengths", []),
            "weaknesses": c1.get("weaknesses", []),
        },
        "candidate_2": {
            "id": c2.get("id"),
            "name": c2.get("name", "Candidate 2"),
            "final_score": c2.get("final_score", 0),
            "keyword_score": c2.get("keyword_score", 0),
            "experience_years": c2.get("experience_years", 0),
            "education": c2.get("education", "—"),
            "skills_found": c2.get("skills_found", []),
            "matched_skills": c2.get("matched_skills", []),
            "missing_skills": c2.get("missing_skills", []),
            "strengths": c2.get("strengths", []),
            "weaknesses": c2.get("weaknesses", []),
        }
    }

    # Generate recommendation
    s1, s2 = c1.get("final_score", 0), c2.get("final_score", 0)
    n1, n2 = c1.get("name", "Candidate 1"), c2.get("name", "Candidate 2")
    e1, e2 = c1.get("experience_years", 0), c2.get("experience_years", 0)
    sk1, sk2 = len(c1.get("matched_skills", [])), len(c2.get("matched_skills", []))

    reasons = []
    winner = None

    if s1 > s2 + 5:
        winner = n1
        reasons.append(f"higher overall match ({s1}% vs {s2}%)")
    elif s2 > s1 + 5:
        winner = n2
        reasons.append(f"higher overall match ({s2}% vs {s1}%)")

    if sk1 > sk2:
        reasons.append(f"more skill matches ({sk1} vs {sk2})")
        if not winner:
            winner = n1
    elif sk2 > sk1:
        reasons.append(f"more skill matches ({sk2} vs {sk1})")
        if not winner:
            winner = n2

    if e1 > e2:
        reasons.append(f"more experience ({e1}y vs {e2}y)")
        if not winner:
            winner = n1
    elif e2 > e1:
        reasons.append(f"more experience ({e2}y vs {e1}y)")
        if not winner:
            winner = n2

    if winner and reasons:
        comparison["recommendation"] = f"{winner} is the stronger candidate due to {', '.join(reasons[:2])}."
    elif abs(s1 - s2) <= 5:
        comparison["recommendation"] = f"Both candidates are closely matched. Consider interviews to differentiate."
    else:
        comparison["recommendation"] = f"Review both profiles carefully — each has distinct advantages."

    return comparison
