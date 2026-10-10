"""Minimal in-memory sliding-window rate limiter (no new dependency).

Per-process and therefore approximate under multiple workers — documented as
a basic abuse guard, not a security boundary. Production deployments behind
Render/NGINX should add edge rate limiting for real protection.
"""
import time
from collections import defaultdict, deque
from functools import wraps

from flask import jsonify, request

_hits = defaultdict(deque)


def limit(per_minute):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if per_minute and per_minute > 0:
                now = time.monotonic()
                key = (request.remote_addr or "unknown", fn.__name__)
                q = _hits[key]
                while q and now - q[0] > 60:
                    q.popleft()
                if len(q) >= per_minute:
                    return jsonify({"error": "Rate limit exceeded. Retry shortly."}), 429
                q.append(now)
            return fn(*a, **kw)
        return wrapper
    return deco


def clear():
    _hits.clear()
