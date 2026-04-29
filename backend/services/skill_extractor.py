"""
Skill Extractor — NLP-powered skill detection from resume text.

Features:
- spaCy PhraseMatcher with 500+ skill patterns
- Alias normalization (ML → Machine Learning, JS → JavaScript, etc.)
- Categorized skill extraction (16 categories)
- Experience year extraction (explicit mentions + date range calculation)
- Education level detection
"""

import json
import os
import re
import logging

import spacy
from spacy.matcher import PhraseMatcher

logger = logging.getLogger(__name__)

# ── Load spaCy model ──────────────────────────────────────────────
nlp = spacy.load("en_core_web_sm")

# ── Load skills dataset ───────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILLS_PATH = os.path.join(BASE_DIR, "data", "skills", "skills_dataset.json")
ALIASES_PATH = os.path.join(BASE_DIR, "data", "skills", "skill_aliases.json")

with open(SKILLS_PATH, "r") as f:
    skills_data = json.load(f)

# Load aliases (abbreviation → canonical form)
skill_aliases = {}
if os.path.exists(ALIASES_PATH):
    with open(ALIASES_PATH, "r") as f:
        skill_aliases = json.load(f)
    logger.info(f"Loaded {len(skill_aliases)} skill aliases")

# Build flat list and category map
all_skills = []
skill_category_map = {}

for category, skills in skills_data.items():
    for skill in skills:
        skill_lower = skill.lower()
        all_skills.append(skill_lower)
        skill_category_map[skill_lower] = category

# Build PhraseMatcher
matcher = PhraseMatcher(nlp.vocab, attr="LOWER")
patterns = [nlp.make_doc(skill) for skill in all_skills]
matcher.add("SKILLS", patterns)

logger.info(f"Loaded {len(all_skills)} skills across {len(skills_data)} categories")


def _normalize_text_with_aliases(text):
    """
    Pre-process text: replace known abbreviations with canonical skill names.
    Uses word-boundary matching to avoid partial replacements.
    """
    if not skill_aliases:
        return text

    normalized = text
    for alias, canonical in skill_aliases.items():
        # Use word boundary regex to replace only whole-word matches
        pattern = r'\b' + re.escape(alias) + r'\b'
        normalized = re.sub(pattern, canonical, normalized, flags=re.IGNORECASE)

    return normalized


def extract_skills(text):
    """
    Extract skills from resume text.
    Normalizes aliases before matching.
    Returns: sorted list of unique found skill strings.
    """
    if not text:
        return []

    # Normalize abbreviations
    normalized_text = _normalize_text_with_aliases(text)

    doc = nlp(normalized_text[:100000])  # spaCy limit safety
    matches = matcher(doc)

    found = set()
    for _, start, end in matches:
        skill = doc[start:end].text.lower()
        found.add(skill)

    return sorted(list(found))


def normalize_skill(skill):
    """
    Normalize a single skill string through the alias system.
    Used to normalize required_skills input to match extracted skills.
    E.g., 'react' → 'react.js', 'aws' → 'amazon web services'
    """
    skill_lower = skill.lower().strip()
    # Check direct alias match
    if skill_lower in skill_aliases:
        canonical = skill_aliases[skill_lower].lower()
        # Return the canonical form if it exists in our skill set
        if canonical in all_skills:
            return canonical
    # If the skill itself is in the dataset, return as-is
    if skill_lower in all_skills:
        return skill_lower
    # Otherwise return the lowered/stripped version
    return skill_lower


def extract_skills_with_categories(text):
    """
    Extract skills grouped by category.
    Returns: dict of {category: [skills]}
    """
    skills = extract_skills(text)
    categorized = {}
    for skill in skills:
        cat = skill_category_map.get(skill, "other")
        categorized.setdefault(cat, []).append(skill)
    return categorized


def extract_experience_years(text):
    """
    Estimate years of experience from resume text.
    Methods:
    1. Explicit mentions: '3 years', '5+ years experience'
    2. Date range calculation: '2019 - 2024' -> 5 years
    """
    if not text:
        return 0

    text_lower = text.lower()
    
    # Try to isolate experience section to avoid counting education years
    experience_text = text_lower
    edu_headers = ['education', 'academic', 'qualification', 'university', 'college']
    exp_headers = ['experience', 'work history', 'employment', 'professional background']
    
    # Simple heuristic: find where Experience starts and Education ends (or vice versa)
    exp_start = -1
    for h in exp_headers:
        pos = text_lower.find(h)
        if pos != -1:
            exp_start = pos
            break
            
    edu_start = -1
    for h in edu_headers:
        pos = text_lower.find(h)
        if pos != -1:
            edu_start = pos
            break

    # If we found both, and education is after experience, truncate at education
    if exp_start != -1 and edu_start != -1 and edu_start > exp_start:
        experience_text = text_lower[exp_start:edu_start]
    elif exp_start != -1:
        experience_text = text_lower[exp_start:]

    max_years = 0

    # Method 1: Explicit mention patterns (check full text as it's often in summary)
    explicit_patterns = [
        r'(\d+)\+?\s*years?\s*of\s*experience',
        r'(\d+)\+?\s*years?\s*experience',
        r'experience\s*(?:of\s*)?(\d+)\+?\s*years?',
        r'(\d+)\+?\s*years?\s*(?:in|of|working)',
        r'(\d+)\+?\s*yrs?\s*(?:of\s*)?(?:experience|exp)',
        r'over\s*(\d+)\s*years?',
    ]
    for pattern in explicit_patterns:
        matches = re.findall(pattern, text_lower)
        for m in matches:
            val = int(m)
            if 0 < val < 50:
                max_years = max(max_years, val)

    # Method 2: Year ranges (only in experience_text if we found one)
    current_year = 2024 
    year_range_patterns = [
        r'(20[0-2]\d)\s*[-–—to]+\s*(20[0-2]\d)',
        r'(20[0-2]\d)\s*[-–—to]+\s*(?:present|current|now|ongoing)',
        r'(19[89]\d)\s*[-–—to]+\s*(20[0-2]\d)',
        r'(19[89]\d)\s*[-–—to]+\s*(?:present|current|now|ongoing)',
    ]

    year_ranges = []
    # Use experience_text for ranges to avoid education years
    search_text = experience_text if exp_start != -1 else text_lower
    
    for pattern in year_range_patterns:
        matches = re.findall(pattern, search_text)
        for m in matches:
            if isinstance(m, tuple):
                start_year = int(m[0])
                end_year = int(m[1]) if len(m) > 1 and m[1].isdigit() else current_year
            else:
                start_year = int(m)
                end_year = current_year
            diff = end_year - start_year
            if 0 < diff < 50:
                year_ranges.append(diff)

    if year_ranges:
        # Instead of summing all (which might overlap), take the longest single range 
        # or a reasonable sum. Here we'll take the max range + some overlap heuristic 
        # but for simplicity, let's take the largest range found in experience.
        # OR we can sum them but cap it.
        total_from_ranges = sum(year_ranges)
        # If we have multiple ranges, they might be consecutive. 
        # But if total is > 30 for a young person, it's wrong.
        max_years = max(max_years, total_from_ranges)

    return min(max_years, 40) # Sanity cap


def extract_education(text):
    """
    Detect education level from resume text.
    Returns the highest education level found.
    """
    if not text:
        return "Not specified"

    text_lower = text.lower()

    education_levels = [
        ("PhD", ['phd', 'ph.d', 'doctorate', 'doctor of philosophy']),
        ("Masters", ['master', 'm.tech', 'm.sc', 'mba', 'mca', 'm.s.',
                      'master of science', 'master of arts', 'master of business',
                      'master of technology', 'm.e.', 'master of engineering',
                      'ms in', 'ma in', 'mtech']),
        ("Bachelors", ['bachelor', 'b.tech', 'b.sc', 'b.e', 'b.com', 'b.a.',
                       'undergraduate', 'btech', 'bachelor of science',
                       'bachelor of arts', 'bachelor of engineering',
                       'bachelor of technology', 'bachelor of commerce',
                       'bs in', 'ba in', 'bca', 'bba']),
        ("Diploma/Certificate", ['diploma', 'certificate', '12th', 'higher secondary',
                                  'associate degree', 'certification', 'bootcamp',
                                  'nanodegree', 'professional certificate']),
    ]

    for level, keywords in education_levels:
        if any(kw in text_lower for kw in keywords):
            return level

    return "Not specified"
