#!/usr/bin/env bash
# research.sh — Data collection → Optimisation → Readiness report
#
# Usage:
#   ./research.sh collect              # record live ticks (run for days)
#   ./research.sh collect --days 7     # reminder: collect for N days (just informational)
#   ./research.sh optimize             # grid-search best parameters on collected data
#   ./research.sh report               # full readiness checklist → go/no-go
#   ./research.sh optimize --pair BTC-USD:ETH-USD
#   ./research.sh report   --capital 10000
# --------------------------------------------------------------------------

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
PY="$BACKEND/.venv/bin/python"
DATA_DIR="$BACKEND/data/ticks"

# ── Colours ─────────────────────────────────────────────────────────────────
RED='\033[0;31m';  GREEN='\033[0;32m';  YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}▸${RESET} $*"; }
success() { echo -e "${GREEN}✔${RESET} $*"; }
warn()    { echo -e "${YELLOW}⚠${RESET}  $*"; }
err()     { echo -e "${RED}✖${RESET} $*" >&2; }
header()  { echo -e "\n${BOLD}${GREEN}━━━  $*  ${RESET}"; }

# ── Defaults ────────────────────────────────────────────────────────────────
PAIR_A="ETH-USD"
PAIR_B="SOL-USD"
CAPITAL=5000
ENTRY_Z="2.0 2.5 3.0"
EXIT_Z="0.2 0.3 0.5"
Z_WINDOWS="40 60 90"
TRAIN_PCT="0.7"

# ── Parse global flags before the subcommand ────────────────────────────────
SUBCOMMAND="${1:-help}"
shift || true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pair)
      IFS=':' read -r PAIR_A PAIR_B <<< "$2"; shift 2 ;;
    --capital)
      CAPITAL="$2"; shift 2 ;;
    --entry-z)
      ENTRY_Z="$2"; shift 2 ;;
    --exit-z)
      EXIT_Z="$2"; shift 2 ;;
    --z-windows)
      Z_WINDOWS="$2"; shift 2 ;;
    --train-pct)
      TRAIN_PCT="$2"; shift 2 ;;
    --days)
      shift 2 ;;   # informational only
    *)
      warn "Unknown flag: $1"; shift ;;
  esac
done

# ── Guard: venv must exist ───────────────────────────────────────────────────
if [ ! -f "$PY" ]; then
  err "Python venv not found at $BACKEND/.venv"
  err "Run ./start.sh once to create it, then try again."
  exit 1
fi

# ── Tick file discovery ──────────────────────────────────────────────────────
count_tick_files() {
  ls "$DATA_DIR"/ticks_*.csv 2>/dev/null | wc -l | tr -d ' '
}

tick_file_args() {
  ls "$DATA_DIR"/ticks_*.csv 2>/dev/null | tr '\n' ' '
}

require_tick_data() {
  local n
  n=$(count_tick_files)
  if [ "$n" -eq 0 ]; then
    err "No tick data found in $DATA_DIR/"
    err "Run:  ./research.sh collect   (leave running for at least 1 day)"
    exit 1
  fi
  local days
  days=$n
  if [ "$days" -lt 7 ]; then
    warn "Only $days day(s) of data collected — aim for ≥ 7 days before optimising."
    warn "More data = more reliable results. Continue? [y/N]"
    read -r yn
    [[ "$yn" =~ ^[Yy]$ ]] || exit 0
  fi
  success "$days day(s) of tick data found."
}

# ============================================================================
case "$SUBCOMMAND" in

# ── collect ─────────────────────────────────────────────────────────────────
collect)
  header "Data Collection Mode"
  echo
  info "Pair      : $PAIR_A / $PAIR_B"
  info "Output    : $DATA_DIR/"
  info "Mode      : record-only (no trading, no DB needed)"
  echo
  warn "Leave this running for as long as possible."
  warn "7 days minimum → 30 days for high confidence results."
  warn "Press Ctrl+C when done."
  echo

  mkdir -p "$DATA_DIR"

  # Check if Redis is up (needed for exchange connections? no, but check anyway)
  redis-cli ping &>/dev/null || warn "Redis not running — that's OK for record-only mode."

  cd "$BACKEND"
  exec "$PY" -m src.main \
    --record-only \
    --data-dir "$DATA_DIR" \
    --log-level INFO
  ;;

# ── optimize ────────────────────────────────────────────────────────────────
optimize)
  header "Parameter Optimisation"
  require_tick_data

  TICK_FILES
  TICK_FILES=$(tick_file_args)

  echo
  info "Pair       : $PAIR_A / $PAIR_B"
  info "Data files : $(count_tick_files) day(s)"
  info "Grid       : entry_z=[${ENTRY_Z}]  exit_z=[${EXIT_Z}]  z_window=[${Z_WINDOWS}]"
  info "Split      : ${TRAIN_PCT} train / $(echo "1 - $TRAIN_PCT" | bc) test (walk-forward)"
  echo
  info "Running grid search — this may take a few minutes..."
  echo

  cd "$BACKEND"
  # shellcheck disable=SC2086
  "$PY" -m backtest.optimize \
    --ticks $TICK_FILES \
    --symbol-a "$PAIR_A" \
    --symbol-b "$PAIR_B" \
    --entry-z $ENTRY_Z \
    --exit-z $EXIT_Z \
    --z-window $Z_WINDOWS \
    --train-pct "$TRAIN_PCT" \
    --balance "$CAPITAL"

  echo
  success "Optimisation complete."
  info "Update backend/config/risk.json with the recommended values above."
  info "Then run:  ./research.sh report   to check if you're ready to go live."
  ;;

# ── report ───────────────────────────────────────────────────────────────────
report)
  header "Live-Money Readiness Report"
  require_tick_data

  TICK_FILES=$(tick_file_args)

  echo
  info "Pair    : $PAIR_A / $PAIR_B"
  info "Capital : \$$CAPITAL"
  info "Data    : $(count_tick_files) day(s) of ticks"
  echo

  cd "$BACKEND"
  # shellcheck disable=SC2086
  if "$PY" -m backtest.report \
    --ticks $TICK_FILES \
    --symbol-a "$PAIR_A" \
    --symbol-b "$PAIR_B" \
    --capital "$CAPITAL" \
    --train-pct "$TRAIN_PCT"; then
    echo
    success "All gates passed. Next steps:"
    echo
    echo -e "  1. Update ${BOLD}backend/config/risk.json${RESET} with optimised parameters"
    echo -e "  2. Run ${BOLD}./start.sh${RESET} and paper-trade for 2 more weeks"
    echo -e "  3. Verify live results match backtest (slippage, fill rates)"
    echo -e "  4. Start with ${BOLD}10–20%${RESET} of intended capital on a real exchange"
    echo -e "  5. Scale up only after another 2 weeks of profitable live trading"
    echo
    warn "Recommended exchanges for pairs trading (low fees + API reliability):"
    warn "  • Binance  — 0.04% maker / 0.06% taker (BNB discount available)"
    warn "  • Kraken   — 0.16% maker / 0.26% taker (high liquidity)"
    warn "  • Bybit    — 0.02% maker / 0.05% taker (best for frequent trading)"
    echo
  else
    echo
    err "Some gates failed. Do NOT use real money yet."
    info "Collect more data:   ./research.sh collect"
    info "Re-optimise:         ./research.sh optimize"
    info "Re-run report:       ./research.sh report"
  fi
  ;;

# ── all ──────────────────────────────────────────────────────────────────────
all)
  header "Full Research Pipeline"
  echo
  warn "This will:"
  echo "  1. Check existing tick data"
  echo "  2. Run parameter optimisation"
  echo "  3. Print the readiness report"
  echo
  info "If you have no data yet, run './research.sh collect' first."
  echo

  require_tick_data

  # Optimise
  bash "$ROOT/research.sh" optimize \
    --pair "$PAIR_A:$PAIR_B" \
    --capital "$CAPITAL" \
    --entry-z "$ENTRY_Z" \
    --exit-z "$EXIT_Z" \
    --z-windows "$Z_WINDOWS" \
    --train-pct "$TRAIN_PCT"

  echo
  # Report
  bash "$ROOT/research.sh" report \
    --pair "$PAIR_A:$PAIR_B" \
    --capital "$CAPITAL" \
    --train-pct "$TRAIN_PCT"
  ;;

# ── status ───────────────────────────────────────────────────────────────────
status)
  header "Data Collection Status"
  echo
  N=$(count_tick_files)
  if [ "$N" -eq 0 ]; then
    warn "No tick data collected yet."
    info "Start collecting:  ./research.sh collect"
  else
    success "$N day(s) of tick data in $DATA_DIR/"
    echo
    for f in "$DATA_DIR"/ticks_*.csv; do
      ROWS=$(wc -l < "$f")
      ROWS=$((ROWS - 1))  # subtract header
      DATE=$(basename "$f" | sed 's/ticks_//' | sed 's/.csv//')
      printf "  %-14s  %s rows\n" "$DATE" "$(printf '%d' $ROWS | sed ':a;s/\B[0-9]\{3\}\>/,&/;ta')"
    done
    echo
    if [ "$N" -lt 7 ]; then
      warn "Collect at least 7 days before optimising (more = better)."
    elif [ "$N" -lt 30 ]; then
      info "Good start. 30 days gives much higher confidence."
    else
      success "Excellent — enough data to optimise and report."
      info "Run:  ./research.sh all   to run the full pipeline."
    fi
  fi
  ;;

# ── help / default ───────────────────────────────────────────────────────────
help|--help|-h|"")
  echo
  echo -e "${BOLD}research.sh${RESET} — Data → Optimise → Readiness → Real money"
  echo
  echo -e "${BOLD}SUBCOMMANDS${RESET}"
  echo "  collect          Record live ticks to CSV (run for days)"
  echo "  optimize         Grid-search best parameters on collected data"
  echo "  report           Full readiness checklist (go / no-go)"
  echo "  all              Optimise + report in one shot"
  echo "  status           Show how much data has been collected"
  echo
  echo -e "${BOLD}GLOBAL FLAGS${RESET}"
  echo "  --pair A:B       Pair to analyse (default: ETH-USD:SOL-USD)"
  echo "  --capital N      Intended real-money amount in USD (default: 5000)"
  echo "  --entry-z 'a b'  Entry z-score values to test (default: '2.0 2.5 3.0')"
  echo "  --exit-z  'a b'  Exit  z-score values to test (default: '0.2 0.3 0.5')"
  echo "  --z-windows 'n'  Z-score window sizes to test (default: '40 60 90')"
  echo "  --train-pct N    Train/test split fraction   (default: 0.7)"
  echo
  echo -e "${BOLD}TYPICAL WORKFLOW${RESET}"
  echo "  1.  ./research.sh collect              # leave running for 7–30 days"
  echo "  2.  ./research.sh status               # check how much data you have"
  echo "  3.  ./research.sh optimize             # find best parameters"
  echo "  4.  ./research.sh report               # pass all 14 gates → go live"
  echo "  # or run steps 3+4 together:"
  echo "      ./research.sh all"
  echo
  echo -e "${BOLD}EXAMPLES${RESET}"
  echo "  ./research.sh collect --pair BTC-USD:ETH-USD"
  echo "  ./research.sh optimize --pair ETH-USD:SOL-USD --capital 10000"
  echo "  ./research.sh report --capital 2500"
  echo "  ./research.sh all --pair BTC-USD:ETH-USD --capital 5000"
  echo
  ;;

*)
  err "Unknown subcommand: $SUBCOMMAND"
  echo "Run './research.sh help' for usage."
  exit 1
  ;;

esac
