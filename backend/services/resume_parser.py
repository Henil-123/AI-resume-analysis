"""
Resume Parser — Extracts structured data from PDF, DOCX, and TXT resumes.

Features:
- Multi-format text extraction (PDF via PyPDF2/pdfminer, DOCX, TXT)
- spaCy NER-based name extraction with heuristic fallback
- Email, phone, LinkedIn extraction
- Confidence scoring (high/medium/low) based on data quality
- Graceful handling of empty/corrupted files
"""

import re
import os
import logging

logger = logging.getLogger(__name__)

# ── spaCy model (loaded once) ────────────────────────────────────
_nlp = None

def _get_nlp():
    global _nlp
    if _nlp is None:
        import spacy
        _nlp = spacy.load("en_core_web_sm")
    return _nlp


# ── Text Extraction ──────────────────────────────────────────────

def extract_text_from_pdf(file_path):
    """Extract raw text from a PDF file. Falls back to pdfminer if PyPDF2 fails."""
    text = ""
    try:
        import PyPDF2
        with open(file_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"
    except Exception as e:
        logger.warning(f"PyPDF2 failed: {e}, trying pdfminer...")
        try:
            from pdfminer.high_level import extract_text
            text = extract_text(file_path)
        except Exception as e2:
            logger.error(f"pdfminer also failed: {e2}")
            text = ""
    return text


def extract_text_from_docx(file_path):
    """Extract raw text from a DOCX file."""
    try:
        from docx import Document
        doc = Document(file_path)
        text = "\n".join([para.text for para in doc.paragraphs])
        return text
    except Exception as e:
        logger.error(f"DOCX extraction failed: {e}")
        return ""


def extract_text(file_path):
    """Auto-detect file type and extract text."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        return extract_text_from_pdf(file_path)
    elif ext in [".docx", ".doc"]:
        return extract_text_from_docx(file_path)
    elif ext == ".txt":
        with open(file_path, "r", errors="ignore") as f:
            return f.read()
    else:
        return ""


# ── Text Cleaning ────────────────────────────────────────────────

def clean_text(text):
    """Clean and normalize extracted resume text."""
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r'[^\w\s\.\+\#\@\-\/]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ── Entity Extraction ────────────────────────────────────────────

def extract_email(text):
    """Extract email address from text."""
    pattern = r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
    matches = re.findall(pattern, text)
    return matches[0] if matches else None


def extract_phone(text):
    """Extract phone number from text (supports international formats)."""
    patterns = [
        r'[\+]?[0-9]{1,3}[-\s\.]?[(]?[0-9]{3}[)]?[-\s\.]?[0-9]{3}[-\s\.]?[0-9]{4,6}',
        r'[(]?[0-9]{3}[)]?[-\s\.]?[0-9]{3}[-\s\.]?[0-9]{4,6}',
        r'[\+]?[0-9]{10,13}',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text)
        if matches:
            # Return the longest match (most complete number)
            return max(matches, key=len).strip()
    return None


def extract_linkedin(text):
    """Extract LinkedIn profile URL from text."""
    patterns = [
        r'(?:https?://)?(?:www\.)?linkedin\.com/in/[\w\-]+/?',
        r'linkedin\.com/in/[\w\-]+/?',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            url = matches[0]
            if not url.startswith("http"):
                url = "https://" + url
            return url
    return None


def _load_known_skills():
    """Load skill names to filter them out of NER name detection."""
    try:
        import json
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        skills_path = os.path.join(base_dir, "data", "skills", "skills_dataset.json")
        if os.path.exists(skills_path):
            with open(skills_path, "r") as f:
                data = json.load(f)
            # Flatten all skill names into a lowercase set
            return {s.lower() for cat_skills in data.values() for s in cat_skills}
    except Exception as e:
        logger.warning(f"Could not load skills for name filter: {e}")
    return set()

_known_skills_for_name_filter = _load_known_skills()

# Common brand/tool names that spaCy misclassifies as PERSON entities
_BRAND_BLACKLIST = {
    "adobe xd", "adobe photoshop", "adobe illustrator", "visual studio",
    "google cloud", "amazon web services", "microsoft azure",
    "apache kafka", "elastic search", "mongo db",
}


def extract_name_ner(text):
    """
    Extract candidate name using spaCy Named Entity Recognition.
    Looks for PERSON entities in the first few lines of the resume.
    Filters out entities that match known skill/tool/brand names.
    """
    try:
        nlp = _get_nlp()
        # Only process first 500 chars (name is usually at the top)
        header_text = text[:500]
        doc = nlp(header_text)
        for ent in doc.ents:
            if ent.label_ == "PERSON":
                name = ent.text.strip().split('\n')[0] # Take only first line
                # Validate: should be 2-5 words, no numbers
                words = name.split()
                if 1 < len(words) <= 5 and not any(c.isdigit() for c in name):
                    # Skip if this "name" is actually a known skill or brand
                    name_lower = name.lower().strip()
                    if name_lower in _known_skills_for_name_filter:
                        logger.debug(f"Skipping NER name '{name}' — matches a known skill")
                        continue
                    if name_lower in _BRAND_BLACKLIST:
                        logger.debug(f"Skipping NER name '{name}' — matches a known brand")
                        continue
                    return name.title()
        return None
    except Exception as e:
        logger.warning(f"NER name extraction failed: {e}")
        return None


def extract_name_heuristic(text):
    """Fallback: Try to extract candidate name from first lines of resume."""
    lines = text.strip().split('\n')
    for line in lines[:5]:
        line = line.strip()
        # Skip empty lines or lines that look like headers/contact info
        if len(line) < 3 or len(line) > 50:
            continue
        
        lower_line = line.lower()
        if any(lower_line.startswith(p) for p in ['email:', 'phone:', 'linkedin:', 'address:', 'github:']):
            continue

        if any(
            c in lower_line for c in ['@', '.com', 'http', '|', '+91', '+1',
                                         'resume', 'curriculum', 'objective',
                                         'summary', 'address', 'phone', 'email']
        ):
            continue

        words = line.split()
        if 1 < len(words) <= 5 and not any(c.isdigit() for c in line):
            return line.title()
            
    return "Unknown Candidate"


def extract_name(text):
    """Extract name: NER first, heuristic fallback."""
    ner_name = extract_name_ner(text)
    if ner_name:
        return ner_name
    return extract_name_heuristic(text)


# ── Main Parser ──────────────────────────────────────────────────

def parse_resume(file_path):
    """
    Main function: parse resume and return structured data.

    Returns dict with:
    - raw_text, clean_text: extracted text
    - name, email, phone, linkedin: contact info
    - word_count: resume length
    - confidence: "high" / "medium" / "low" based on extraction quality
    - parse_error: set if resume couldn't be parsed properly
    """
    raw_text = extract_text(file_path)

    # Handle empty or too-short resumes
    if not raw_text or len(raw_text.strip()) < 50:
        return {
            "raw_text": raw_text or "",
            "clean_text": "",
            "name": "Unknown Candidate",
            "email": None,
            "phone": None,
            "linkedin": None,
            "word_count": 0,
            "confidence": "low",
            "parse_error": "Resume text too short or unreadable"
        }

    clean = clean_text(raw_text)
    name = extract_name(raw_text)
    email = extract_email(raw_text)
    phone = extract_phone(raw_text)
    linkedin = extract_linkedin(raw_text)

    # Calculate confidence based on how much data we extracted
    found_count = sum(1 for x in [
        name != "Unknown Candidate",
        email is not None,
        phone is not None
    ] if x)

    if found_count >= 3:
        confidence = "high"
    elif found_count >= 2:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "raw_text": raw_text,
        "clean_text": clean,
        "name": name,
        "email": email,
        "phone": phone,
        "linkedin": linkedin,
        "word_count": len(clean.split()),
        "confidence": confidence,
        "parse_error": None
    }
