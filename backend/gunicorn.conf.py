"""Gunicorn production config (Render free tier: 512 MB RAM, shared CPU).

Why this shape (see Logs 2026-10-13: `[CRITICAL] WORKER TIMEOUT` killed the
single sync worker mid-analysis, and every later request — including /health
— queued behind the dead worker as 502s):

- 1 worker: all free-tier RAM goes to one process, no duplication.
- 2 threads: /health and the status dot keep answering while one analysis
  occupies the other thread. Threads share memory, so this costs ~nothing.
- timeout 300: a cold analysis (spin-down wake ~50 s + spaCy load + full
  explainable pipeline on 0.5 CPU) can exceed the old 180 s budget; killing
  the worker mid-request helped nobody. Exceeding requests now log loudly
  via worker_abort instead of dying silently.
- post_worker_init warmup: heavy models load ONCE per worker, single
  threaded, before serving. (An earlier @app.before_request warmup raced the
  request thread into a double spaCy load and OOM-crashed the worker —
  never warm from request path.)
"""
import os

bind = "0.0.0.0:" + os.environ.get("PORT", "5000")
workers = 1
threads = 2
worker_class = "gthread"
timeout = 300
graceful_timeout = 30
preload_app = True
keepalive = 5


def post_worker_init(worker):
    """Warm the instant TF-IDF engine before the worker serves traffic.

    spaCy is deliberately NOT imported here: on 2026-10-13 a boot-time
    `import services.skill_extractor` never finished (no 'warmup done' in
    300 s under free-tier memory pressure) and the worker was killed before
    serving a single request. spaCy loads lazily on first use instead, with
    the download fallback time-boxed (see skill_extractor._load_nlp), so a
    slow load degrades rather than hangs the boot.
    """
    try:
        worker.log.info("warming tfidf engine...")
        from services import job_matcher as _jm
        _jm._get_embed_model()
        _jm._embed_batch(["warmup probe"])
        worker.log.info("model warmup done (engine=%s)", _jm.embed_status())
    except Exception as e:
        worker.log.warning("model warmup failed (%s); first request slower",
                           type(e).__name__)


def worker_abort(worker):
    worker.log.error("worker aborted after %ss on a request; "
                     "free-tier analysis exceeded budget", timeout)
