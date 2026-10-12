"""Frontend wiring guards: browse must work even if JS partially fails."""
import os
import re
import shutil

FRONTEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "..", "frontend")
APP_JS = os.path.join(FRONTEND, "app.js")
INDEX = os.path.join(FRONTEND, "index.html")
CSS = os.path.join(FRONTEND, "styles.css")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_app_js_parses():
    """A single SyntaxError kills the whole script (browse included)."""
    if shutil.which("node") is None:
        import pytest
        pytest.skip("node unavailable for syntax check")
        return
    import subprocess
    r = subprocess.run(["node", "--check", APP_JS], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:500]


def test_no_duplicate_const_in_explain_sections():
    """Regression: duplicate `const gaps` in explainSections broke all wiring."""
    src = _read(APP_JS)
    start = src.find("function explainSections(c)")
    assert start != -1
    end = src.find("\n}\n", start)
    body = src[start:end]
    decls = re.findall(r"\bconst\s+([A-Za-z_$][\w$]*)", body)
    dupes = {d for d in decls if decls.count(d) > 1}
    assert not dupes, f"duplicate declarations: {dupes}"


def test_drop_zone_is_native_label_fallback():
    """Browse must not depend solely on JS: #drop-zone is a <label for=...>."""
    html = _read(INDEX)
    m = re.search(r'<label[^>]*id="drop-zone"[^>]*>', html)
    assert m, "drop-zone must be a <label>"
    assert 'for="file-input"' in m.group(0)
    assert re.search(r'<input[^>]*id="file-input"[^>]*>', html)


def test_file_input_not_display_none():
    """display:none inputs ignore clicks in some browsers; visually hide only."""
    css = _read(CSS)
    blocks = re.findall(r"#file-input\s*\{([^}]*)\}", css)
    assert blocks, "#file-input needs a hiding rule"
    assert "display:none" not in blocks[0].replace(" ", "")


def test_init_guards_missing_elements():
    """One missing element must not kill the rest of the wiring."""
    src = _read(APP_JS)
    dz_fn = src[src.find("function initDropZone()"):src.find("function initDropZone()") + 900]
    assert "if (!dz || !fi) return" in dz_fn
    init = src[src.find("document.addEventListener('DOMContentLoaded'"):]
    assert "if (dz && fi)" in init
    assert "if (brand)" in init


def test_analysis_gated_on_backend_health():
    """runAnalysis must check /health first, not fire blind into a 502."""
    src = _read(APP_JS)
    assert "async function checkBackend(" in src
    run = src[src.find("async function runAnalysis()"):src.find("async function runAnalysis()") + 2500]
    assert "await checkBackend(" in run
    assert "Your files are still selected" in run


def test_footer_note_is_hosting_aware():
    """The 'local backend, nothing sent' note must not lie on the hosted site."""
    src = _read(APP_JS)
    assert "nothing is sent anywhere" in src  # local case kept
    assert "uploaded resumes are sent there" in src  # hosted case honest


def test_status_dot_wakes_backend():
    """Red dot must be actionable: click re-checks with wake retries."""
    src = _read(APP_JS)
    assert "async function wakeBackend(" in src
    assert "for (let i = 1; i <= 3; i++)" in src
    init = src[src.find("document.addEventListener('DOMContentLoaded'"):]
    assert "wakeBackend" in init


def test_no_in_worker_eager_warmup():
    """No before_request model warmup: it OOM-crashed the free-tier worker."""
    import os as _os
    app_py = _os.path.join(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__))), "app.py")
    with open(app_py, encoding="utf-8") as f:
        src = f.read()
    assert "_ensure_warmed_in_worker" not in src
    assert "\n@app.before_request" not in src
