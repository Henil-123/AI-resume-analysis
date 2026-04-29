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

# Install requirements
echo "📦 Installing Python dependencies (this takes ~2 minutes)..."
pip install -q -r requirements.txt

# Download spaCy model
echo "🤖 Downloading spaCy NLP model..."
python -m spacy download en_core_web_sm -q

# Copy .env
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "📝 Created .env file (edit it to add MongoDB URI)"
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
