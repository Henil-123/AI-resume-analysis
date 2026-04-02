"""
Resume Parser — Extracts structured data from PDF, DOCX, and TXT resumes.

Features:
- Multi-format text extraction (PDF via PyPDF2/pdfminer, DOCX, TXT)
- Regex-based name extraction with heuristic fallback (no spaCy needed)
- Email, phone, LinkedIn extraction
- Confidence scoring (high/medium/low) based on data quality
- Graceful handling of empty/corrupted files

Note: Replaced spaCy NER with regex heuristics for Vercel serverless compatibility.
"""

import re
import os
import logging

logger = logging.getLogger(__name__)


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


def extract_name_heuristic(text):
    """Extract candidate name from first lines of resume using heuristics."""
    lines = text.strip().split('\n')
    for line in lines[:5]:
        line = line.strip()
        if len(line) > 2 and len(line) < 50 and not any(
            c in line.lower() for c in ['@', '.com', 'http', '|', '+91', '+1',
                                         'resume', 'curriculum', 'objective',
                                         'summary', 'address', 'phone', 'email']
        ):
            words = line.split()
            if 1 < len(words) <= 5 and not any(c.isdigit() for c in line):
                return line.title()
    return "Unknown Candidate"


def extract_name(text):
    """Extract name using heuristic approach."""
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
