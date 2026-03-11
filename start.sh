#!/usr/bin/env bash
# start.sh — Start the full Arbitrage Paper Trading system
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
LOG_DIR="$ROOT/.logs"
mkdir -p "$LOG_DIR"

# ── Colours ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*"; }

# ── Cleanup on exit ─────────────────────────────────────────────────────────
PIDS=()
cleanup() {
  echo ""
  info "Shutting down..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  success "All processes stopped."
}
trap cleanup EXIT INT TERM

# ── 1. Start Redis ───────────────────────────────────────────────────────────
info "Starting Redis..."
if redis-cli ping &>/dev/null 2>&1; then
  success "Redis already running"
else
  brew services start redis &>/dev/null
  sleep 1
  if redis-cli ping &>/dev/null 2>&1; then
    success "Redis started"
  else
    error "Redis failed to start. Install with: brew install redis"
    exit 1
  fi
fi

# ── 2. Start PostgreSQL ──────────────────────────────────────────────────────
PG_BIN="/opt/homebrew/opt/postgresql@17/bin"
info "Starting PostgreSQL..."
if "$PG_BIN/pg_isready" -q 2>/dev/null; then
  success "PostgreSQL already running"
else
  brew services start postgresql@17 &>/dev/null
  sleep 2
  if "$PG_BIN/pg_isready" -q 2>/dev/null; then
    success "PostgreSQL started"
  else
    error "PostgreSQL failed to start. Install with: brew install postgresql@17"
    exit 1
  fi
fi

# Ensure database exists
"$PG_BIN/createdb" arbitrage 2>/dev/null || true

# ── 3. Start Python backend ──────────────────────────────────────────────────
info "Starting backend..."
if [ ! -d "$BACKEND/.venv" ]; then
  warn "Python venv not found. Creating..."
  python3 -m venv "$BACKEND/.venv"
  "$BACKEND/.venv/bin/pip" install -q --upgrade pip
  "$BACKEND/.venv/bin/pip" install -q -e "$BACKEND/.[dev]"
fi

cd "$BACKEND" && "$BACKEND/.venv/bin/python" -m src.main --log-level WARNING \
  > "$LOG_DIR/backend.log" 2>&1 &
cd "$ROOT"
BACKEND_PID=$!
PIDS+=("$BACKEND_PID")

# Wait for API to be ready
info "Waiting for API server..."
for i in $(seq 1 15); do
  if curl -s http://localhost:8000/api/stats &>/dev/null; then
    success "Backend API ready at http://localhost:8000"
    break
  fi
  sleep 1
  if [ "$i" -eq 15 ]; then
    error "Backend failed to start. Check $LOG_DIR/backend.log"
    cat "$LOG_DIR/backend.log" | tail -20
    exit 1
  fi
done

# ── 4. Start Next.js frontend ────────────────────────────────────────────────
info "Starting frontend..."
if [ ! -d "$FRONTEND/node_modules" ]; then
  warn "node_modules not found. Installing..."
  npm --prefix "$FRONTEND" install --silent
fi

npm --prefix "$FRONTEND" run dev \
  > "$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!
PIDS+=("$FRONTEND_PID")

# Wait for Next.js to be ready
info "Waiting for frontend..."
for i in $(seq 1 20); do
  if curl -s http://localhost:3000 &>/dev/null; then
    success "Frontend ready at http://localhost:3000"
    break
  fi
  sleep 1
  if [ "$i" -eq 20 ]; then
    error "Frontend failed to start. Check $LOG_DIR/frontend.log"
    cat "$LOG_DIR/frontend.log" | tail -20
    exit 1
  fi
done

# ── 5. Open browser ──────────────────────────────────────────────────────────
open http://localhost:3000 2>/dev/null || true

echo ""
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}  Arbitrage Dashboard running${RESET}"
echo -e "  Dashboard:  ${CYAN}http://localhost:3000${RESET}"
echo -e "  API:        ${CYAN}http://localhost:8000/api/stats${RESET}"
echo -e "  Logs:       ${CYAN}$LOG_DIR/${RESET}"
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "  Press ${BOLD}Ctrl+C${RESET} to stop all services"
echo ""

# Keep running, tail logs in background
tail -f "$LOG_DIR/backend.log" 2>/dev/null &

# Wait for all child processes
wait "${BACKEND_PID}" "${FRONTEND_PID}" 2>/dev/null || true
