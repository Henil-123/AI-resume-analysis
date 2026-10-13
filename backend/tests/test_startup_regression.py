"""Regression guards for the 2026-10-13 Render 502 outage.

Measured import costs on this machine:
    import sentence_transformers   81.6s   <- the killer (transformers stack)
    import spacy (pulls thinc)      5.5s
    spacy.load('en_core_web_sm')    3.3s
    import torch (via thinc)        3.1s

`get_embed_model_metadata()` used to `import sentence_transformers` on every
scoring request just to read `__version__`, so one analysis burned ~80s
before doing any work and blew the gunicorn worker timeout on the 512MB /
0.5-CPU free tier (worker killed -> 502 for the whole service).

These tests pin the fix. Note the assertion is on sentence_transformers,
not torch: torch is dragged in by spaCy's own thinc dependency and is only
3.1s, so it is allowed here. Production removes torch from the image
altogether (see test_production_image_excludes_torch).
"""
import os
import subprocess
import sys
import time

os.environ["SEMANTIC_ENGINE"] = "tfidf"

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RESUME = """Aarav Sharma
aarav.sharma@example.com
EDUCATION
B.Tech, Computer Science, 2021 - 2025
KEY SKILLS
Java, React, Spring Boot, SQL
PROJECTS
Portal (2024 - 2025): React frontend, Spring Boot REST backend on MySQL.
"""

JD = """Job Title: Java Full Stack Developer
Required Skills: Java, Spring Boot, SQL, React, TypeScript.
Education Requirements: Minimum qualification: BE/BTech in Computer Science.
"""


def _probe(script):
    """Run `script` in a fresh interpreter, return (stdout, seconds, stderr)."""
    t0 = time.time()
    p = subprocess.run([sys.executable, "-c", script], cwd=BACKEND,
                       capture_output=True, text=True)
    return p.stdout.strip(), time.time() - t0, p.stderr


def test_tfidf_scoring_never_imports_sentence_transformers():
    """Core regression: a full TF-IDF analysis must not load the ML stack."""
    out, _, err = _probe(
        "import os, sys, io\n"
        "os.environ['SEMANTIC_ENGINE']='tfidf'\n"
        "import app\n"
        "app.app.config['TESTING']=True\n"
        "c=app.app.test_client()\n"
        "r=c.post('/api/v1/analyses', data={'resume':(io.BytesIO(%r.encode()),'r.txt'),"
        "'job_description':%r,'required_experience':'0'},"
        "content_type='multipart/form-data')\n"
        "print('status', r.status_code)\n"
        "print('st_loaded', 'sentence_transformers' in sys.modules)\n"
        % (RESUME, JD))
    assert "status 201" in out, out + "\n" + err[-800:]
    assert "st_loaded False" in out, (
        "sentence_transformers was imported during a TF-IDF analysis "
        "(~80s of import inside a request):\n" + out)


def test_version_lookup_is_instant_and_import_free():
    """Provenance lookup must use dist metadata, not `import sentence_transformers`."""
    out, _, err = _probe(
        "import sys, time\n"
        "from services import job_matcher as jm\n"
        "jm._ST_VERSION_CACHE.clear()\n"
        "t=time.time(); v=jm._sentence_transformers_version(); dt=time.time()-t\n"
        "print('version', v)\n"
        "print('seconds %.3f' % dt)\n"
        "print('st_loaded', 'sentence_transformers' in sys.modules)\n"
    )
    assert "st_loaded False" in out, out + "\n" + err[-500:]
    seconds = float([l for l in out.splitlines()
                     if l.startswith("seconds")][0].split()[1])
    assert seconds < 2.0, f"version lookup took {seconds}s (the 80s import is back)"


def test_health_endpoint_does_not_load_spacy():
    """/health is Render's deploy probe: answering it must stay cheap."""
    out, seconds, err = _probe(
        "import sys, os\n"
        "os.environ['SEMANTIC_ENGINE']='tfidf'\n"
        "import app\n"
        "app.app.config['TESTING']=True\n"
        "r=app.app.test_client().get('/health')\n"
        "print('status', r.status_code)\n"
        "print('spacy_imported', 'services.skill_extractor' in sys.modules)\n"
    )
    assert "status 200" in out, out + "\n" + err[-500:]
    assert "spacy_imported False" in out, (
        "/health imported skill_extractor, forcing a spaCy load:\n" + out)
    assert seconds < 15, f"/health took {seconds}s on a cold process"


def test_production_image_excludes_torch():
    """requirements.txt stays torch-free; ML deps live in requirements-ml.txt."""
    req = os.path.join(BACKEND, "requirements.txt")
    ml = os.path.join(BACKEND, "requirements-ml.txt")
    with open(req, encoding="utf-8") as f:
        core_lines = [ln.strip() for ln in f
                      if ln.strip() and not ln.strip().startswith("#")]
    with open(ml, encoding="utf-8") as f:
        ml_txt = f.read()
    core = "\n".join(core_lines)
    assert "sentence-transformers" not in core, \
        "torch/sentence-transformers must stay out of the production image"
    assert "torch" not in core.lower(), \
        "no torch-pinned package may be in the production image"
    assert "sentence-transformers" in ml_txt
    assert "-r requirements.txt" in ml_txt