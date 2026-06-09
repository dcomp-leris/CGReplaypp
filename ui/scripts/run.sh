#!/usr/bin/env bash
# Run both backend and frontend in one terminal (uses tmux or plain background jobs)
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"

GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'

echo -e "\n${BLUE}Starting CGReplay UI...${NC}"

# Start backend
cd "$ROOT/backend"
source .venv/bin/activate
python server.py &
BACKEND_PID=$!
echo -e "${GREEN}  Backend started (PID $BACKEND_PID) → http://localhost:8000${NC}"

sleep 1

# Start frontend dev server
cd "$ROOT/frontend"
npm run dev &
FRONTEND_PID=$!
echo -e "${GREEN}  Frontend started (PID $FRONTEND_PID) → http://localhost:3000${NC}"

echo -e "\n${GREEN}  Open http://localhost:3000 in your browser${NC}"
echo -e "  Press Ctrl+C to stop both servers\n"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; echo 'Stopped.'" EXIT
wait
