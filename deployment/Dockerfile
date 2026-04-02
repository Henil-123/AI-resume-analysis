FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m spacy download en_core_web_sm

COPY backend/ ./backend/
COPY data/ ./data/

WORKDIR /app/backend

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

EXPOSE 5000

# Production mode: debug off, 2 workers
ENV APP_ENV=production
ENV PORT=5000

CMD gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 120 app:app
