"""
Job Matcher — Scoring engine for resume-to-job matching.

Features:
- Keyword-based skill matching (set intersection)
- Lightweight semantic similarity using TF-IDF + cosine (no PyTorch needed)
- Experience scoring with configurable requirements
- Weighted final score with full component breakdown
- Weight profiles support (Technical/Manager/Entry-Level)

Note: Replaced Sentence-BERT with TF-IDF cosine similarity for Vercel compatibility.
"""

import os
import json
import hashlib
import math
import re
import logging
from collections import Counter

logger = logging.getLogger(__name__)

# ── TF-IDF Semantic Similarity (lightweight, no ML models) ───────

_jd_cache = {}
_MAX_CACHE_SIZE = 100


def _tokenize(text):
    """Simple tokenizer: lowercase, remove punctuation, split into words."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    words = text.split()
    # Remove common stop words
    stop_words = {
        'a', 'an', 'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'of', 'with', 'by', 'from', 'is', 'are', 'was', 'were', 'be', 'been',
        'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
        'could', 'should', 'may', 'might', 'shall', 'can', 'this', 'that',
        'these', 'those', 'i', 'me', 'my', 'we', 'our', 'you', 'your', 'he',
        'she', 'it', 'they', 'them', 'their', 'what', 'which', 'who', 'whom',
        'how', 'when', 'where', 'why', 'not', 'no', 'nor', 'if', 'then',
        'than', 'too', 'very', 'just', 'about', 'above', 'after', 'again',
        'all', 'also', 'am', 'as', 'because', 'before', 'between', 'both',
        'each', 'few', 'more', 'most', 'other', 'over', 'same', 'so', 'some',
        'such', 'up', 'out', 'only', 'own', 'into', 'here', 'there'
    }
    return [w for w in words if w not in stop_words and len(w) > 1]


def _cosine_similarity(vec1, vec2):
    """Compute cosine similarity between two counter vectors."""
    if not vec1 or not vec2:
        return 0.0

    # Get all unique terms
    all_terms = set(vec1.keys()) | set(vec2.keys())

    dot_product = sum(vec1.get(t, 0) * vec2.get(t, 0) for t in all_terms)
    mag1 = math.sqrt(sum(v ** 2 for v in vec1.values()))
    mag2 = math.sqrt(sum(v ** 2 for v in vec2.values()))

    if mag1 == 0 or mag2 == 0:
        return 0.0

    return dot_product / (mag1 * mag2)


# ── Weight Profiles ──────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROFILES_PATH = os.path.join(BASE_DIR, "data", "weight_profiles.json")

DEFAULT_WEIGHTS = {
    "skills_weight": 0.6,
    "semantic_weight": 0.3,
    "experience_weight": 0.1
}


def get_weight_profiles():
    """Load all available weight profiles."""
    if os.path.exists(PROFILES_PATH):
        with open(PROFILES_PATH, "r") as f:
            return json.load(f)
    return {"technical": DEFAULT_WEIGHTS}


def _get_weights(profile=None):
    """Get weights for a given profile name, or defaults."""
    if profile:
        profiles = get_weight_profiles()
        if profile in profiles:
            return profiles[profile]
        logger.warning(f"Profile '{profile}' not found, using defaults")
    return DEFAULT_WEIGHTS


# ── Skill Matching ───────────────────────────────────────────────

def calculate_match_score(resume_skills, required_skills):
    """
    Keyword-based match score.
    Score = (matched skills / required skills) * 100
    Returns: float 0–100
    """
    if not required_skills:
        return 0.0

    resume_set = set(s.lower().strip() for s in resume_skills)
    required_set = set(s.lower().strip() for s in required_skills if s.strip())

    if not required_set:
        return 0.0

    matched = resume_set.intersection(required_set)
    score = (len(matched) / len(required_set)) * 100
    return round(score, 1)


def get_matched_skills(resume_skills, required_skills):
    """Return list of skills that matched."""
    resume_set = set(s.lower().strip() for s in resume_skills)
    required_set = set(s.lower().strip() for s in required_skills)
    return sorted(list(resume_set.intersection(required_set)))


def get_missing_skills(resume_skills, required_skills):
    """Return list of required skills missing from resume."""
    resume_set = set(s.lower().strip() for s in resume_skills)
    required_set = set(s.lower().strip() for s in required_skills)
    return sorted(list(required_set - resume_set))


# ── Semantic Scoring ─────────────────────────────────────────────

def calculate_semantic_score(resume_text, job_description):
    """
    Lightweight semantic similarity using TF-IDF cosine similarity.
    Returns: float 0–100
    """
    if not resume_text or not job_description:
        return 0.0

    try:
        resume_tokens = _tokenize(resume_text[:2000])
        jd_tokens = _tokenize(job_description[:2000])

        resume_vec = Counter(resume_tokens)
        jd_vec = Counter(jd_tokens)

        similarity = _cosine_similarity(resume_vec, jd_vec)

        # Scale up since TF-IDF cosine tends to give lower values than BERT
        # Cap at 100
        scaled = min(similarity * 130, 1.0)
        return round(scaled * 100, 1)
    except Exception as e:
        logger.error(f"Semantic scoring error: {e}")
        return 0.0


# ── Final Score ──────────────────────────────────────────────────

def calculate_score_breakdown(keyword_score, semantic_score, experience_years,
                               required_experience=0, profile=None):
    """
    Weighted final score combining all signals.
    Returns a FULL BREAKDOWN dict instead of a single float.

    Default weights: skills 60% + semantic 30% + experience 10%
    Weights change based on profile (technical/manager/entry).
    """
    weights = _get_weights(profile)
    sw = weights["skills_weight"]
    sem_w = weights["semantic_weight"]
    exp_w = weights["experience_weight"]

    # Experience score (0-100)
    exp_score = 100.0
    if required_experience > 0:
        if experience_years >= required_experience:
            exp_score = 100.0
        else:
            exp_score = (experience_years / required_experience) * 100

    # If semantic score is 0 (model unavailable), redistribute weight to keyword
    if semantic_score == 0:
        effective_sw = sw + sem_w
        effective_sem_w = 0
    else:
        effective_sw = sw
        effective_sem_w = sem_w

    # Weighted component scores
    skills_component = round(keyword_score * effective_sw, 1)
    semantic_component = round(semantic_score * effective_sem_w, 1)
    experience_component = round(exp_score * exp_w, 1)

    final = round(min(skills_component + semantic_component + experience_component, 100.0), 1)

    return {
        "final_score": final,
        "skills_score": skills_component,
        "semantic_score": semantic_component,
        "experience_score": experience_component,
        "keyword_pct": round(keyword_score, 1),
        "semantic_pct": round(semantic_score, 1),
        "experience_pct": round(exp_score, 1),
        "weights_used": {
            "skills": effective_sw,
            "semantic": effective_sem_w,
            "experience": exp_w
        },
        "profile": profile or "default"
    }


# ── Backward compat wrapper ──────────────────────────────────────

def calculate_final_score(keyword_score, semantic_score, experience_years,
                           required_experience=0, profile=None):
    """
    Legacy wrapper — returns just the final score float.
    Use calculate_score_breakdown() for full details.
    """
    breakdown = calculate_score_breakdown(
        keyword_score, semantic_score,
        experience_years, required_experience, profile
    )
    return breakdown["final_score"]


# ── Recommendation ───────────────────────────────────────────────

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
