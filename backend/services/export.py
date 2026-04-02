"""
CSV Export — Generate downloadable CSV files from candidate data.
"""

import csv
import io


def export_candidates_csv(candidates):
    """
    Generate a CSV string from a list of candidate dicts.

    Args:
        candidates: list of candidate dicts

    Returns:
        string containing CSV data
    """
    if not candidates:
        return ""

    output = io.StringIO()

    fieldnames = [
        "name", "email", "phone", "education",
        "final_score", "keyword_pct", "semantic_pct", "experience_pct",
        "skills_matched", "skills_total", "missing_skills",
        "experience_years", "status", "recommendation",
        "confidence", "created_at"
    ]

    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
    writer.writeheader()

    for candidate in candidates:
        # Flatten nested data for CSV
        score_breakdown = candidate.get("score_breakdown", {})
        recommendation = candidate.get("recommendation", {})
        matched_skills = candidate.get("matched_skills", [])
        missing_skills = candidate.get("missing_skills", [])

        row = {
            "name": candidate.get("name", "Unknown"),
            "email": candidate.get("email", ""),
            "phone": candidate.get("phone", ""),
            "education": candidate.get("education", ""),
            "final_score": candidate.get("final_score", 0),
            "keyword_pct": score_breakdown.get("keyword_pct", 0),
            "semantic_pct": score_breakdown.get("semantic_pct", 0),
            "experience_pct": score_breakdown.get("experience_pct", 0),
            "skills_matched": len(matched_skills),
            "skills_total": len(matched_skills) + len(missing_skills),
            "missing_skills": "; ".join(missing_skills),
            "experience_years": candidate.get("experience_years", 0),
            "status": candidate.get("status", "pending"),
            "recommendation": recommendation.get("label", ""),
            "confidence": candidate.get("confidence", ""),
            "created_at": candidate.get("created_at", ""),
        }
        writer.writerow(row)

    return output.getvalue()
