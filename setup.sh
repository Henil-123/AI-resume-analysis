#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  AI Resume Analyzer — One-command setup
#  Run this from the project root: bash setup.sh
# ─────────────────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   AI Resume Analyzer — Setup Script      ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
  echo "❌ Python 3 not found. Install from https://python.org"
  exit 1
fi
echo "✅ Python found: $(python3 --version)"

# Go to backend
cd backend

# Create virtual environment
if [ ! -d "venv" ]; then
  echo "📦 Creating virtual environment..."
  python3 -m venv venv
fi

# Activate venv
source venv/bin/activate 2>/dev/null || source venv/Scripts/activate 2>/dev/null

# Install requirements (includes the pinned spaCy model wheel,
// so no separate `spacy download` step is needed)
echo "📦 Installing Python dependencies (this takes ~2 minutes)..."
pip install -q -r requirements.txt

# Verify spaCy model (installed via requirements wheel)
echo "🤖 Verifying spaCy NLP model..."
python -c "import spacy; spacy.load('en_core_web_sm'); print('spaCy model OK')" 2>/dev/null \
  || echo "⚠️  spaCy model missing — the app will auto-download it on first run, or use the blank-pipeline fallback"

# Copy .env
if [ ! -f ".env" ]; then
  if [ -f ".env.example" ]; then
    cp .env.example .env
    echo "📝 Created .env file (edit it to add MongoDB URI)"
  else
    echo "⚠️  No .env.example found — skipping .env creation"
  fi
fi

cd ..
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   ✅ Setup complete!                      ║"
echo "╠══════════════════════════════════════════╣"
echo "║  Start backend:                           ║"
echo "║    cd backend && source venv/bin/activate ║"
echo "║    python app.py                          ║"
echo "║                                           ║"
echo "║  Open frontend:                           ║"
echo "║    Open frontend/index.html in browser    ║"
echo "╚══════════════════════════════════════════╝"
echo ""
