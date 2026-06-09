# ============================================================
#  CGReplay UI — Windows Setup Script (PowerShell)
#  Run from repo root: .\scripts\setup_windows.ps1
# ============================================================
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Step($msg)  { Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Ok($msg)    { Write-Host "  OK  $msg" -ForegroundColor Green }
function Warn($msg)  { Write-Host "  !   $msg" -ForegroundColor Yellow }

Step "CGReplay UI - Windows Setup"

# ── Python ────────────────────────────────────────────────────────────────
Step "Checking Python"
try {
    $pyver = python --version 2>&1
    Ok $pyver
} catch {
    Warn "Python not found. Download from https://www.python.org/downloads/"
    exit 1
}

# ── Node ──────────────────────────────────────────────────────────────────
Step "Checking Node.js"
try {
    $nodever = node --version 2>&1
    Ok "Node $nodever"
} catch {
    Warn "Node.js not found. Download from https://nodejs.org"
    exit 1
}

# ── Backend ───────────────────────────────────────────────────────────────
Step "Installing Python dependencies"
Set-Location "$root\backend"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -q --upgrade pip
pip install -q -r requirements.txt
Ok "Backend ready"
deactivate

# ── Frontend ──────────────────────────────────────────────────────────────
Step "Installing frontend dependencies"
Set-Location "$root\frontend"
npm install --silent
Ok "Frontend ready"

Step "Building frontend"
npm run build
Ok "Frontend built"

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host "  Terminal 1 (backend):"
Write-Host "    cd $root\backend"
Write-Host "    .\.venv\Scripts\Activate.ps1"
Write-Host "    python server.py"
Write-Host ""
Write-Host "  Terminal 2 (frontend):"
Write-Host "    cd $root\frontend"
Write-Host "    npm run dev"
Write-Host ""
Write-Host "  Open browser: http://localhost:3000"
Write-Host "================================================================" -ForegroundColor Green
