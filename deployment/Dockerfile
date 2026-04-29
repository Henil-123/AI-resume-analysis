FROM python:3.11-slim

WORKDIR /app

# Install CPU-only PyTorch first (much smaller than full torch with CUDA)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m spacy download en_core_web_sm

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
