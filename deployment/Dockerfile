FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install spaCy model directly via pip (more reliable than `spacy download` on Render)
RUN pip install --no-cache-dir https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.7.1/en_core_web_sm-3.7.1-py3-none-any.whl

COPY backend/ ./backend/
COPY data/ ./data/

WORKDIR /app/backend

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

EXPOSE 5000

# Production mode: debug off, 1 worker to save memory on free tier
ENV APP_ENV=production
ENV PORT=5000

CMD gunicorn --bind 0.0.0.0:$PORT --workers 1 --timeout 300 --preload app:app
