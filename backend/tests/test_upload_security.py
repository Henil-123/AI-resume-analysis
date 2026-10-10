"""Upload + retrieval security: types, sizes, malformed files, traversal."""
import io
import os

os.environ["SEMANTIC_ENGINE"] = "tfidf"

import app as appmod

JD = "Job Title: Java Developer. Required Skills: Java, SQL."


def _client():
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def _post_resume(c, content, filename, jd=JD):
    return c.post("/api/v1/analyses",
                  data={"resume": (io.BytesIO(content), filename),
                        "job_description": jd, "required_experience": "0"},
                  content_type="multipart/form-data")


def test_rejects_disallowed_extension():
    c = _client()
    r = _post_resume(c, b"print('x')", "evil.exe")
    assert r.status_code == 400
    assert "not supported" in r.get_json()["error"]


def test_rejects_fake_pdf_header():
    c = _client()
    r = _post_resume(c, b"this is not a pdf at all", "resume.pdf")
    assert r.status_code in (400, 422)
    assert "header" in r.get_json()["error"].lower()


def test_rejects_empty_file():
    c = _client()
    r = _post_resume(c, b"", "resume.txt")
    assert r.status_code in (400, 422)


def test_rejects_encrypted_pdf():
    from PyPDF2 import PdfWriter
    import tempfile
    w = PdfWriter()
    w.add_blank_page(200, 200)
    w.encrypt("secret")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        w.write(f)
        path = f.name
    with open(path, "rb") as f:
        content = f.read()
    os.remove(path)
    c = _client()
    r = _post_resume(c, content, "locked.pdf")
    assert r.status_code in (400, 422)
    assert "encrypt" in r.get_json()["error"].lower()


def test_rejects_textless_pdf_with_guidance():
    from PyPDF2 import PdfWriter
    import tempfile
    w = PdfWriter()
    w.add_blank_page(200, 200)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        w.write(f)
        path = f.name
    with open(path, "rb") as f:
        content = f.read()
    os.remove(path)
    c = _client()
    r = _post_resume(c, content, "blank.pdf")
    assert r.status_code in (400, 422)


def test_path_traversal_filename_is_neutralized():
    c = _client()
    body = "Aarav Sharma\naarav@example.com\nKEY SKILLS\nJava, SQL\n"
    r = _post_resume(c, body.encode(), "../../etc/passwd.txt")
    assert r.status_code in (201, 400)
    if r.status_code == 201:
        aid = r.get_json()["analysis_id"]
        stored = appmod._get_stored_analysis(aid)
        assert ".." not in (stored.get("resume_file") or "")
        assert "/" not in (stored.get("resume_file") or "")


def test_oversized_upload_rejected():
    c = _client()
    big = b"a" * (appmod.app.config["MAX_CONTENT_LENGTH"] + 1024)
    r = _post_resume(c, big, "big.txt")
    assert r.status_code in (400, 413, 422)


def test_unknown_analysis_access_has_no_leak():
    c = _client()
    for url in ("/api/v1/analyses/" + "d" * 32,
                "/api/v1/analyses/" + "d" * 32 + "/report?format=json",
                "/api/v1/analyses/" + "d" * 32 + "/report?format=pdf",
                "/candidates/" + "d" * 32):
        r = c.get(url)
        assert r.status_code == 404, url
        assert "traceback" not in r.get_data(as_text=True).lower()


def test_pdf_report_of_missing_analysis_is_404_not_500():
    c = _client()
    r = c.get("/api/v1/analyses/" + "e" * 32 + "/report?format=pdf")
    assert r.status_code == 404
