#!/usr/bin/env bash
# ============================================================
#  CGReplay UI — Linux/macOS Setup & Run Script
#  Usage: bash scripts/setup_linux.sh
# ============================================================
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

banner() { echo -e "\n${BLUE}▶ $1${NC}"; }
ok()     { echo -e "${GREEN}  ✓ $1${NC}"; }
warn()   { echo -e "${YELLOW}  ! $1${NC}"; }

banner "CGReplay UI — Setup"

# ── Python check ────────────────────────────────────────────────────────────
banner "Checking Python (3.9+)"
if ! command -v python3 &>/dev/null; then
  echo "python3 not found. Install it: sudo apt install python3"
  exit 1
fi
ok "Python $(python3 --version)"

# ── Node check ──────────────────────────────────────────────────────────────
banner "Checking Node.js (18+)"
if ! command -v node &>/dev/null; then
  warn "Node.js not found. Install from https://nodejs.org or:"
  echo "  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -"
  echo "  sudo apt install -y nodejs"
  exit 1
fi
ok "Node $(node --version)"

# ── Backend deps ─────────────────────────────────────────────────────────────
banner "Installing Python backend dependencies"
cd "$ROOT/backend"
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
ok "Backend dependencies installed"
deactivate

# ── Frontend deps ─────────────────────────────────────────────────────────────
banner "Installing frontend dependencies"
cd "$ROOT/frontend"
npm install --silent
ok "Frontend dependencies installed"

# ── Optional: build frontend into backend ────────────────────────────────────
banner "Building frontend"
npm run build
ok "Frontend built to frontend/build"

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  Setup complete! To run:                     ║${NC}"
echo -e "${GREEN}║                                              ║${NC}"
echo -e "${GREEN}║  Terminal 1 (backend):                       ║${NC}"
echo -e "${GREEN}║    cd $ROOT/backend       ║${NC}"
echo -e "${GREEN}║    source .venv/bin/activate                 ║${NC}"
echo -e "${GREEN}║    python server.py                          ║${NC}"
echo -e "${GREEN}║                                              ║${NC}"
echo -e "${GREEN}║  Terminal 2 (frontend dev):                  ║${NC}"
echo -e "${GREEN}║    cd $ROOT/frontend      ║${NC}"
echo -e "${GREEN}║    npm run dev                               ║${NC}"
echo -e "${GREEN}║                                              ║${NC}"
echo -e "${GREEN}║  Open: http://localhost:3000                 ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
