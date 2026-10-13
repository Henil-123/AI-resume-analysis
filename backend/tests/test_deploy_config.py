"""Deploy-config regression guards (fast, no models, no network).

Covers the 2026-10-13 outage: the single sync worker with a 180 s timeout
was killed mid-analysis (`[CRITICAL] WORKER TIMEOUT`), and /health then
queued behind the dead worker, so the whole service read as down.
"""
import os
import re

REPO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "..")
DOCKERFILE = os.path.join(REPO, "deployment", "Dockerfile")
CONF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "gunicorn.conf.py")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_gunicorn_conf_is_valid_python():
    import py_compile
    py_compile.compile(CONF, doraise=True)


def test_worker_survives_long_cold_analysis():
    """timeout >= 300: cold free-tier analyses exceed the old 180 s budget."""
    src = _read(CONF)
    m = re.search(r"^timeout\s*=\s*(\d+)", src, re.M)
    assert m and int(m.group(1)) >= 300, "gunicorn timeout must be >= 300s"


def test_health_answers_mid_analysis():
    """threads >= 2 on one worker: /health responds while analysis runs."""
    src = _read(CONF)
    assert re.search(r"^workers\s*=\s*1\b", src, re.M), "free tier: exactly 1 worker"
    m = re.search(r"^threads\s*=\s*(\d+)", src, re.M)
    assert m and int(m.group(1)) >= 2, "need >= 2 threads for health-during-analysis"


def test_models_warm_before_serving_not_per_request():
    """post_worker_init warms once; per-request warmup OOM-crashed the worker."""
    src = _read(CONF)
    assert "def post_worker_init" in src
    assert "skill_extractor" in src  # spacy loads before traffic, not in it


def test_docker_uses_the_conf():
    src = _read(DOCKERFILE)
    assert "gunicorn.conf.py" in src, "Docker must boot via gunicorn.conf.py"
    assert "--timeout 180" not in src, "old 180s inline timeout must be gone"
