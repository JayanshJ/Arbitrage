#!/usr/bin/env bash
# start.sh — Start the full Arbitrage Paper Trading system
#
# Usage:
#   ./start.sh            # start everything, stream logs to terminal
#   ./start.sh --quiet    # start everything, suppress streamed logs
#   ./start.sh --stop     # kill any running backend / frontend processes
# --------------------------------------------------------------------------

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
LOG_DIR="$ROOT/.logs"
mkdir -p "$LOG_DIR"

# ── Colours ─────────────────────────────────────────────────────────────────
RED='\033[0;31m';  GREEN='\033[0;32m';  YELLOW='\033[1;33m'
CYAN='\033[0;36m'; MAGENTA='\033[0;35m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}    $*"; }
success() { echo -e "${GREEN}[OK]${RESET}      $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}    $*"; }
err()     { echo -e "${RED}[ERROR]${RESET}   $*"; }
section() { echo -e "\n${BOLD}${GREEN}── $* ${RESET}"; }

# ── Parse flags ─────────────────────────────────────────────────────────────
QUIET=false
for arg in "$@"; do
  case "$arg" in
    --quiet) QUIET=true ;;
    --stop)
      info "Stopping backend and frontend..."
      pkill -f "src.main" 2>/dev/null && success "Backend stopped" || warn "Backend not running"
      pkill -f "next dev"  2>/dev/null && success "Frontend stopped" || warn "Frontend not running"
      exit 0
      ;;
    --help|-h)
      echo "Usage: $0 [--quiet] [--stop]"
      echo "  --quiet  Suppress streamed log output"
      echo "  --stop   Kill running processes and exit"
      exit 0
      ;;
  esac
done

# ── Cleanup on Ctrl+C / exit ─────────────────────────────────────────────────
PIDS=()
cleanup() {
  echo ""
  info "Shutting down all processes..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  # Kill the log-tailer subshells
  kill 0 2>/dev/null || true
  wait 2>/dev/null || true
  success "All stopped. Bye!"
}
trap cleanup EXIT INT TERM

# ── Helper: stream a log file with a coloured prefix ─────────────────────────
stream_log() {
  local file="$1"
  local prefix="$2"
  local color="$3"
  # Wait for the file to appear then tail it
  while [ ! -f "$file" ]; do sleep 0.2; done
  tail -n 0 -f "$file" 2>/dev/null | while IFS= read -r line; do
    echo -e "${color}${prefix}${RESET}  ${line}"
  done &
}

# ── Helper: wait for an HTTP endpoint to respond ─────────────────────────────
wait_for_http() {
  local url="$1"
  local label="$2"
  local max="${3:-20}"
  for i in $(seq 1 "$max"); do
    if curl -sf "$url" -o /dev/null 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  err "$label did not become ready after ${max}s"
  return 1
}

# ═══════════════════════════════════════════════════════════════════════════
section "Infrastructure"
# ═══════════════════════════════════════════════════════════════════════════

# Redis
info "Checking Redis..."
if redis-cli ping &>/dev/null; then
  success "Redis already running"
else
  brew services start redis &>/dev/null
  sleep 1
  if redis-cli ping &>/dev/null; then
    success "Redis started"
  else
    err "Redis failed to start. Run: brew install redis"
    exit 1
  fi
fi

# PostgreSQL
PG_BIN="/opt/homebrew/opt/postgresql@17/bin"
info "Checking PostgreSQL..."
if "$PG_BIN/pg_isready" -q 2>/dev/null; then
  success "PostgreSQL already running"
else
  brew services start postgresql@17 &>/dev/null
  sleep 2
  if "$PG_BIN/pg_isready" -q 2>/dev/null; then
    success "PostgreSQL started"
  else
    err "PostgreSQL failed to start. Run: brew install postgresql@17"
    exit 1
  fi
fi

# Ensure the database exists
"$PG_BIN/createdb" arbitrage 2>/dev/null && info "Created database 'arbitrage'" || true

# ═══════════════════════════════════════════════════════════════════════════
section "Backend"
# ═══════════════════════════════════════════════════════════════════════════

if [ ! -d "$BACKEND/.venv" ]; then
  warn "Python venv not found — creating it now (one-time, takes ~30 s)..."
  python3 -m venv "$BACKEND/.venv"
  "$BACKEND/.venv/bin/pip" install -q --upgrade pip
  "$BACKEND/.venv/bin/pip" install -q -e "$BACKEND/.[dev]"
  success "Python venv ready"
fi

# Kill any stale backend process on port 8000
lsof -ti :8000 | xargs kill -9 2>/dev/null || true

BACKEND_LOG="$LOG_DIR/backend.log"
: > "$BACKEND_LOG"   # truncate

cd "$BACKEND"
.venv/bin/python -m src.main >> "$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!
PIDS+=("$BACKEND_PID")
cd "$ROOT"

if $QUIET; then
  info "Backend starting (logs: $BACKEND_LOG)"
else
  stream_log "$BACKEND_LOG" "[backend]" "$MAGENTA"
fi

info "Waiting for API to be ready..."
if wait_for_http "http://localhost:8000/api/pairs/status" "Backend API" 25; then
  success "Backend API live at http://localhost:8000"
else
  err "Backend failed — last 30 lines of $BACKEND_LOG:"
  tail -30 "$BACKEND_LOG"
  exit 1
fi

# ═══════════════════════════════════════════════════════════════════════════
section "Frontend"
# ═══════════════════════════════════════════════════════════════════════════

if [ ! -d "$FRONTEND/node_modules" ]; then
  warn "node_modules not found — installing (one-time, takes ~60 s)..."
  npm --prefix "$FRONTEND" install --silent
  success "npm packages installed"
fi

# Kill any stale process on port 3000
lsof -ti :3000 | xargs kill -9 2>/dev/null || true

FRONTEND_LOG="$LOG_DIR/frontend.log"
: > "$FRONTEND_LOG"

npm --prefix "$FRONTEND" run dev >> "$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!
PIDS+=("$FRONTEND_PID")

if $QUIET; then
  info "Frontend starting (logs: $FRONTEND_LOG)"
else
  stream_log "$FRONTEND_LOG" "[frontend]" "$CYAN"
fi

info "Waiting for Next.js to be ready..."
if wait_for_http "http://localhost:3000" "Frontend" 30; then
  success "Frontend live at http://localhost:3000"
else
  err "Frontend failed — last 30 lines of $FRONTEND_LOG:"
  tail -30 "$FRONTEND_LOG"
  exit 1
fi

# ═══════════════════════════════════════════════════════════════════════════

open "http://localhost:3000" 2>/dev/null || true

echo ""
echo -e "${BOLD}${GREEN}┌─────────────────────────────────────────────────────┐${RESET}"
echo -e "${BOLD}${GREEN}│        Arbitrage Dashboard is running  🚀           │${RESET}"
echo -e "${BOLD}${GREEN}├─────────────────────────────────────────────────────┤${RESET}"
echo -e "${BOLD}${GREEN}│${RESET}  Dashboard  →  ${CYAN}http://localhost:3000${RESET}"
echo -e "${BOLD}${GREEN}│${RESET}  API        →  ${CYAN}http://localhost:8000/api/stats${RESET}"
echo -e "${BOLD}${GREEN}│${RESET}  Logs       →  ${CYAN}$LOG_DIR/${RESET}"
echo -e "${BOLD}${GREEN}│${RESET}"
echo -e "${BOLD}${GREEN}│${RESET}  Press ${BOLD}Ctrl+C${RESET} to stop everything"
echo -e "${BOLD}${GREEN}└─────────────────────────────────────────────────────┘${RESET}"
echo ""

# Wait forever (cleanup trap fires on Ctrl+C)
wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
