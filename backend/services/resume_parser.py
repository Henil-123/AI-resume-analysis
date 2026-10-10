import re
import os
import mimetypes

ALLOWED_EXTENSIONS = {"pdf", "docx", "doc", "txt"}
ALLOWED_MIME = {
    ".pdf": {"application/pdf"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/zip"},
    ".doc": {"application/msword"},
    ".txt": {"text/plain"},
}
# Very short extraction is suspicious (scanned/image-only PDF, garbled file).
MIN_WORDS_FOR_VALID_DOC = 10


def validate_upload(file_path, max_mb=10):
    """MIME + size + readability guards. Raises ValueError with a clear message."""
    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported file type '.{ext}'. Use PDF, DOCX, or TXT")
    size = os.path.getsize(file_path)
    if size == 0:
        raise ValueError("File is empty (0 bytes)")
    if size > max_mb * 1024 * 1024:
        raise ValueError(f"File exceeds {max_mb}MB limit")
    mime, _ = mimetypes.guess_type(file_path)
    allowed = ALLOWED_MIME.get("." + ext, set())
    if mime and allowed and mime not in allowed and ext != ".txt":
        raise ValueError(f"Unexpected file content (MIME {mime}) for extension {ext}")
    return True


def extract_pages_from_pdf(file_path):
    """Extract text page by page: [(page_number, text)]. Preserves page refs."""
    pages = []
    try:
        import PyPDF2
        with open(file_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for i, page in enumerate(reader.pages, start=1):
                try:
                    t = page.extract_text() or ""
                except Exception:
                    t = ""
                pages.append((i, t))
        if any(t.strip() for _, t in pages):
            return pages
    except Exception as e:
        print(f"PyPDF2 failed: {e}, trying pdfminer...")
    try:
        from pdfminer.high_level import extract_pages as _pdf_pages
        from pdfminer.layout import LTTextContainer
        pages = []
        for i, layout in enumerate(_pdf_pages(file_path), start=1):
            buf = []
            for el in layout:
                if isinstance(el, LTTextContainer):
                    buf.append(el.get_text())
            pages.append((i, "".join(buf)))
        return pages
    except Exception as e2:
        print(f"pdfminer also failed: {e2}")
        return pages or [(1, "")]


def extract_text_from_pdf(file_path):
    """Extract raw text from a PDF file (all pages joined)."""
    pages = extract_pages_from_pdf(file_path)
    return "\n".join(t for _, t in pages)


def extract_text_from_docx(file_path):
    """Extract raw text from a DOCX file."""
    try:
        from docx import Document
        doc = Document(file_path)
        text = "\n".join([para.text for para in doc.paragraphs])
        return text
    except Exception as e:
        print(f"DOCX extraction failed: {e}")
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


def clean_text(text):
    """Clean and normalize extracted resume text."""
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r'[^\w\s\.\+\#]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def extract_email(text):
    """Extract email address from text."""
    pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    matches = re.findall(pattern, text)
    return matches[0] if matches else None


def extract_phone(text):
    """Extract phone number from text."""
    pattern = r'[\+]?[(]?[0-9]{3}[)]?[-\s\.]?[0-9]{3}[-\s\.]?[0-9]{4,6}'
    matches = re.findall(pattern, text)
    return matches[0] if matches else None


def extract_links(text):
    """Extract LinkedIn / GitHub / portfolio URLs."""
    links = {}
    m = re.search(r'(https?://)?(www\.)?linkedin\.com/[^\s,;)]+', text, re.IGNORECASE)
    if m:
        links["linkedin"] = m.group(0)
    m = re.search(r'(https?://)?(www\.)?github\.com/[^\s,;)]+', text, re.IGNORECASE)
    if m:
        links["github"] = m.group(0)
    return links


def extract_name(text):
    """Try to extract candidate name from first lines of resume."""
    lines = text.strip().split('\n')
    skip_tokens = ['@', '.com', 'http', '|', '+91', '+1', 'resume', 'curriculum', 'cv',
                   'linkedin', 'github', 'phone', 'email', 'address']
    for line in lines[:8]:
        line = line.strip().strip('|-,•·')
        low = line.lower()
        if len(line) < 3 or len(line) > 50:
            continue
        if any(t in low for t in skip_tokens):
            continue
        if re.search(r'\d', line):
            continue
        words = line.split()
        if 1 < len(words) <= 5 and all(w[0].isupper() or w.isupper() for w in words if w):
            return line.title()
        if 1 < len(words) <= 4 and len(words) == len(re.findall(r'[A-Z][a-z]+|[A-Z]+', line)):
            return line.title()
    return "Unknown Candidate"


def estimate_experience_from_dates(text):
    """Estimate experience from year ranges like 2019-2023 / 2020-Present.

    Tolerates text cleaning: `clean_text` strips punctuation, so "2022-Present"
    may arrive as "2022 present". The separator is therefore optional.
    """
    current_year = 2026
    max_span = 0
    # e.g. 2019 - 2023, 2019–Present, 2019 to 2022, 2022 Present (cleaned)
    pattern = r'(19|20)\d{2}\s*(?:-|–|—|to)?\s*((?:19|20)\d{2}|present|current|now)\b'
    for m in re.finditer(pattern, text, re.IGNORECASE):
        start = int(m.group(0)[:4])
        end_raw = m.group(2).lower()
        end = current_year if not end_raw[:2].isdigit() else int(end_raw[:4])
        span = end - start
        if 0 < span < 50:
            max_span = max(max_span, span)
    # single start year with present, e.g. "since 2019"
    m2 = re.search(r'since\s+((?:19|20)\d{2})', text, re.IGNORECASE)
    if m2:
        span = current_year - int(m2.group(1))
        if 0 < span < 50:
            max_span = max(max_span, span)
    return max_span


def parse_resume(file_path):
    """Main function: parse resume and return structured data.

    Adds page-level mapping ({pages: [{page, text, char_start}], so evidence
    can cite its source page) and extraction status flags. Never treats an
    empty extraction as a valid resume — word_count == 0 signals failure.
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        pages = extract_pages_from_pdf(file_path)
        raw_text = "\n".join(t for _, t in pages)
    else:
        raw_text = extract_text(file_path)
        pages = [(1, raw_text)] if raw_text else [(1, "")]
    clean = clean_text(raw_text)
    links = extract_links(raw_text)
    words = len(clean.split())
    page_map, offset = [], 0
    for num, t in pages:
        page_map.append({"page": num, "chars": len(t), "char_start": offset})
        offset += len(t) + 1
    non_empty = sum(1 for _, t in pages if t.strip())
    if words == 0:
        status = "empty"
    elif words < MIN_WORDS_FOR_VALID_DOC:
        status = "suspiciously-short"
    elif ext == ".pdf" and non_empty == 0:
        status = "image-only-pdf"
    else:
        status = "ok"
    out = {
        "raw_text": raw_text,
        "clean_text": clean,
        "email": extract_email(raw_text),
        "phone": extract_phone(raw_text),
        "name": extract_name(raw_text),
        "links": links,
        "word_count": words,
        "pages": [{"page": n, "text": t} for n, t in pages],
        "page_map": page_map,
        "extraction_status": status,
    }
    if status in ("empty", "image-only-pdf"):
        out["ocr_hint"] = ("No text could be extracted — the PDF may be scanned. "
                           "Upload a text-based PDF or paste the content as TXT.")
    elif status == "suspiciously-short":
        out["ocr_hint"] = "Very little text extracted; the score will be low-confidence."
    return out
