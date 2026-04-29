# AI Resume Analyzer — Windows Setup Script
# Run this from the project root in PowerShell: .\setup.ps1

Write-Host ""
Write-Host "╔══════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║   AI Resume Analyzer — Windows Setup     ║" -ForegroundColor Green
Write-Host "╚══════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""

# Check Python
$pythonCmd = "python"
try {
    python --version
} catch {
    try {
        python3 --version
        $pythonCmd = "python3"
    } catch {
        Write-Host "❌ Python not found. Please install Python from https://python.org" -ForegroundColor Red
        exit
    }
}
Write-Host "✅ Python found."

# Go to backend
Set-Location backend

# Create virtual environment
if (-not (Test-Path "venv")) {
    Write-Host "📦 Creating virtual environment..."
    Invoke-Expression "$pythonCmd -m venv venv"
}

# Activate venv and install requirements
Write-Host "📦 Installing Python dependencies (this may take a few minutes)..."
& "venv/Scripts/python.exe" -m pip install -r requirements.txt

# Download spaCy model
Write-Host "🤖 Downloading spaCy NLP model..."
& "venv/Scripts/python.exe" -m spacy download en_core_web_sm

# Copy .env
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "📝 Created .env file."
}

Set-Location ..

Write-Host ""
Write-Host "╔══════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║   ✅ Setup complete!                      ║" -ForegroundColor Green
Write-Host "╠══════════════════════════════════════════╣" -ForegroundColor Green
Write-Host "║  Start backend:                           ║"
Write-Host "║    cd backend                             ║"
Write-Host "║    .\venv\Scripts\activate                ║"
Write-Host "║    python app.py                          ║"
Write-Host "║                                           ║"
Write-Host "║  Open frontend:                           ║"
Write-Host "║    Open frontend\index.html in browser    ║"
Write-Host "╚══════════════════════════════════════════╝"
Write-Host ""
