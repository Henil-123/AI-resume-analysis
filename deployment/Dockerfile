FROM python:3.11-slim

WORKDIR /app

# Install dependencies (requirements includes the spaCy model as a
# pinned wheel — more reliable than `spacy download` on Render)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download embedding weights at build time so the first analysis
# doesn't time out fetching ~200MB on Render's free tier
ENV HF_HOME=/app/.hf-cache SENTENCE_TRANSFORMERS_HOME=/app/.hf-cache
RUN python -c "from sentence_transformers import SentenceTransformer; [SentenceTransformer(m) for m in ('BAAI/bge-small-en-v1.5','all-MiniLM-L6-v2')]"

COPY backend/ ./backend/
COPY data/ ./data/

WORKDIR /app/backend

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

EXPOSE 5000

# Production: 1 worker to fit free-tier memory, long timeout for inference
ENV APP_ENV=production
ENV PORT=5000

CMD gunicorn --bind 0.0.0.0:$PORT --workers 1 --timeout 300 --preload app:app
