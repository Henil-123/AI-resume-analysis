FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies (requirements includes the spaCy model as a
# pinned wheel — more reliable than `spacy download` on Render)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY data/ ./data/

WORKDIR /app/backend

# Health check (generous start period: spaCy + engine warm-up)
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

EXPOSE 5000

# Production: single worker fits free-tier RAM; TF-IDF semantic engine
# avoids loading torch/transformers into memory on small hosts.
# (Local dev leaves SEMANTIC_ENGINE unset to use the BERT engine.)
ENV APP_ENV=production \
    PORT=5000 \
    SEMANTIC_ENGINE=tfidf

CMD gunicorn --bind 0.0.0.0:$PORT --workers 1 --timeout 180 --preload app:app
