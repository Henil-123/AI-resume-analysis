"""
Explainer — Auto-generates human-readable explanations for candidate scores.

Produces:
- strengths: what makes this candidate good
- weaknesses: what's missing or concerning
- warnings: data quality or red-flag issues
- verdict: one-line summary for the recruiter
"""

import logging

logger = logging.getLogger(__name__)


def generate_explanation(result):
    """
    Generate a human-readable explanation for a candidate's score.

    Args:
        result: dict with keys like final_score, score_breakdown, matched_skills,
                missing_skills, experience_years, confidence, skills_by_category, etc.

    Returns:
        dict with: strengths, weaknesses, warnings, verdict
    """
    strengths = []
    weaknesses = []
    warnings = []

    final_score = result.get("final_score", 0)
    score_breakdown = result.get("score_breakdown", {})
    matched = result.get("matched_skills", [])
    missing = result.get("missing_skills", [])
    skills_found = result.get("skills_found", [])
    experience = result.get("experience_years", 0)
    confidence = result.get("confidence", "medium")
    education = result.get("education", "Not specified")
    required_experience = result.get("required_experience", 0)
    skills_by_category = result.get("skills_by_category", {})

    skills_matched_count = len(matched)
    skills_total = skills_matched_count + len(missing)

    # ── Strengths ─────────────────────────────────────────────────
    if skills_matched_count > 0:
        matched_str = ", ".join(matched[:5])
        if skills_matched_count > 5:
            matched_str += f" (+{skills_matched_count - 5} more)"
        strengths.append(f"Strong in: {matched_str}")

    if skills_total > 0 and skills_matched_count / skills_total >= 0.8:
        strengths.append(f"Excellent skill coverage: {skills_matched_count}/{skills_total} required skills")
    elif skills_total > 0 and skills_matched_count / skills_total >= 0.6:
        strengths.append(f"Good skill coverage: {skills_matched_count}/{skills_total} required skills")

    if experience >= 5:
        strengths.append(f"Strong experience: {experience} years")
    elif experience >= 3:
        strengths.append(f"Solid experience: {experience} years")

    if education in ["PhD", "Masters"]:
        strengths.append(f"Advanced education: {education}")

    semantic_pct = score_breakdown.get("semantic_pct", 0)
    if semantic_pct >= 70:
        strengths.append("High resume-job semantic alignment")

    # Strong in specific high-value categories
    for cat in ["data_science", "ml_frameworks", "cloud", "devops"]:
        cat_skills = skills_by_category.get(cat, [])
        if len(cat_skills) >= 3:
            strengths.append(f"Deep {cat.replace('_', ' ')} skills: {', '.join(cat_skills[:3])}")

    if len(skills_found) >= 15:
        strengths.append(f"Broad skill set: {len(skills_found)} skills detected")

    # ── Weaknesses ────────────────────────────────────────────────
    if missing:
        critical_missing = missing[:3]
        weaknesses.append(f"Missing: {', '.join(critical_missing)}")
        if len(missing) > 3:
            weaknesses.append(f"{len(missing) - 3} more required skills not found")

    if skills_total > 0 and skills_matched_count / skills_total < 0.4:
        weaknesses.append(f"Low skill match: only {skills_matched_count}/{skills_total} required skills")

    if required_experience > 0 and experience < required_experience:
        gap = required_experience - experience
        weaknesses.append(f"Experience gap: {experience} years (need {required_experience}, short by {gap})")

    if semantic_pct < 40 and semantic_pct > 0:
        weaknesses.append("Low resume-job alignment — resume may not match role focus")

    if education == "Not specified":
        weaknesses.append("Education not detected in resume")

    if len(skills_found) < 3:
        weaknesses.append("Very few skills detected — resume may lack technical detail")

    # ── Warnings ──────────────────────────────────────────────────
    if confidence == "low":
        warnings.append("Low extraction confidence — some resume data may be missing or incorrect")

    word_count = result.get("word_count", 0)
    if word_count < 100:
        warnings.append(f"Very short resume ({word_count} words) — may lack detail")
    elif word_count > 2000:
        warnings.append(f"Very long resume ({word_count} words) — may benefit from trimming")

    if result.get("parse_error"):
        warnings.append(f"Parser issue: {result['parse_error']}")

    if not result.get("email") and not result.get("phone"):
        warnings.append("No contact information found in resume")

    # ── Verdict ───────────────────────────────────────────────────
    if final_score >= 80:
        if len(weaknesses) == 0:
            verdict = "Excellent candidate — strongly recommend for interview"
        else:
            verdict = "Strong candidate overall, minor gaps noted"
    elif final_score >= 60:
        if len(missing) <= 2:
            verdict = "Good candidate — meets most requirements, worth considering"
        else:
            verdict = "Decent candidate but has notable skill gaps"
    elif final_score >= 40:
        verdict = "Partial match — review carefully, significant gaps exist"
    else:
        if confidence == "low":
            verdict = "Low score, but extraction quality was poor — manual review recommended"
        else:
            verdict = "Weak match — does not meet core requirements"

    return {
        "strengths": strengths,
        "weaknesses": weaknesses,
        "warnings": warnings,
        "verdict": verdict
    }
