# Algorithmic Arbitrage — Statistical Pairs Trading

A production-grade **statistical arbitrage (pairs trading)** system for crypto markets. Trades mean-reversion on cointegrated pairs (e.g. ETH/SOL, BTC/ETH) with full cost accounting, risk management, a live dashboard, and a research pipeline to safely transition from paper trading to real money.

---

## Features

### Strategy Engine

- **Cointegration-based pairs trading** — continuously tracks the log-price spread between two correlated assets; enters when the z-score diverges beyond a configurable threshold and exits when it mean-reverts
- **OLS hedge ratio** — position sizes are β-weighted (`qty_b = notional × β / price_b`) for market-neutral exposure instead of naive equal-dollar sizing
- **Live cointegration revalidation** — every 500 ticks the engine re-runs the full Engle-Granger + ADF + Ornstein-Uhlenbeck half-life test; pairs that break down are disabled automatically and any open positions are force-closed
- **Configurable z-score thresholds** — `entry_z` (default ±2.5 σ) and `exit_z` (default ±0.3 σ) tuned per pair via the research pipeline
- **Half-life gate** — only trades pairs whose spread mean-reverts within 2–168 hours; pairs with too-fast or too-slow reversion are rejected
- **Dual pair support** — ETH-USD/SOL-USD and BTC-USD/ETH-USD run concurrently with independent state

### Risk Management

- **Max drawdown halt** — automatically halts all trading if equity drops more than 15% from its peak; requires manual reset after investigation
- **Position size limit** — each leg is capped at 20% of current balance, divided across max concurrent positions
- **Max concurrent positions** — at most 2 open pairs at once
- **Stop-loss** — force-closes any position where |z-score| extends to ≥ 4 σ (diverging, not reverting)
- **Max hold time** — force-closes stale positions held longer than 7 days
- **Stale price rejection** — ignores any ticker older than 2 seconds
- **Trade cooldown** — 120-second cooldown between entries on the same pair to avoid over-trading

### Real Cost Model

- **Taker fees** — 0.05% per leg per exchange (open + close = 4 fee events per trade)
- **Slippage** — 0.02% per leg (4× per round trip)
- **Funding rate** — 0.01% per 8-hour period on the futures leg, scales with hold time
- **Break-even calculator** — computes the minimum gross P&L fraction required to profit after all costs for any given hold duration

### Exchange Connections

- **Binance** — WebSocket feed (spot + perpetual futures)
- **Kraken** — WebSocket feed
- **Coinbase** — WebSocket feed
- **Async reconnect** — all clients reconnect automatically on disconnect
- **Symbol mapping** — config-driven via `backend/config/symbols.json`; each exchange uses its own ticker format (e.g. `ETHUSDT`, `ETH/USD`, `ETH-USD`)

### Data Pipeline

- **Live tick recorder** (`--record-only` mode) — writes mid-price, best bid, and best ask to daily rotating CSVs in `backend/data/ticks/` without connecting to the database or executing trades; safe to run indefinitely
- **OHLCV downloader** (`backtest/fetch_data.py`) — pulls historical klines from Binance's public REST API, no API key required; configurable interval (1m–1d) and lookback
- **Automatic daily rotation** — each day's ticks land in a new `ticks_YYYY-MM-DD.csv` file

### Backtesting

- **Event-driven backtest engine** (`backtest/engine.py`) — replays collected ticks through the exact same z-score / cointegration logic as the live engine for apples-to-apples comparison
- **Full cost deduction** — fees, slippage, and funding are subtracted from every backtest trade, matching the live cost model exactly
- **Equity curve tracking** — balance is updated tick-by-tick; used for drawdown and Sharpe calculations
- **Per-trade log** — every trade is saved to CSV with entry/exit prices, z-scores, hold time, gross P&L, costs, and net P&L

### Parameter Optimisation

- **Walk-forward grid search** (`backtest/optimize.py`) — splits data into train (70%) and test (30%) windows; grid-searches `entry_z`, `exit_z`, `z_window`, and `stop_loss_z`; validates the top-5 in-sample results on out-of-sample data to prevent overfitting
- **Composite score** — ranks results by Sharpe ratio minus a drawdown penalty; requires at least 10 trades to qualify
- **Ready-to-paste output** — prints the recommended parameter block for `backend/config/risk.json`

### Readiness Report (14-gate go/no-go)

`backtest/report.py` runs every check before you put real money at risk:

| Category | Gate |
|---|---|
| Statistics | Cointegration p-value < 0.05 |
| Statistics | ADF spread stationarity p-value < 0.05 |
| Statistics | Half-life ≥ 2 h |
| Statistics | Half-life ≤ 7 days |
| In-sample | ≥ 30 trades |
| In-sample | Win rate ≥ 55% |
| In-sample | Sharpe ≥ 1.0 (annualised) |
| In-sample | Max drawdown ≤ 15% |
| Out-of-sample | ≥ 10 trades |
| Out-of-sample | Win rate ≥ 50% |
| Out-of-sample | Sharpe ≥ 0.5 |
| Out-of-sample | Max drawdown ≤ 20% |
| Out-of-sample | Positive return |
| Costs | Average net P&L per trade > $0 |

### Telegram Alerts

- **Trade open** — pair, direction, z-score, notional, hedge ratio, half-life
- **Trade close** — pair, net P&L, hold time, close reason
- **Pair disabled** — triggered when live revalidation fails
- **Risk halt** — immediate notification when the drawdown limit is breached
- **Startup** — confirms which pairs are active and current balance
- Silent no-op when `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` are not set

### REST API

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/pairs/status` | Current z-scores, signals, open positions, balance |
| `GET` | `/api/pairs/trades` | Paginated trade history (newest first) |
| `GET` | `/api/pairs/stream` | SSE stream — live z-score + balance data (1 s interval) |
| `GET` | `/api/risk` | Risk manager state: halt status, limits, peak capital |
| `POST` | `/api/risk/reset-halt` | Manually clear a risk halt after investigation |

### Live Dashboard (Next.js)

- **Connection indicator** — live/disconnected pill with animated pulse
- **Stats cards** — current balance, total P&L, total trades, and per-pair z-score + signal summary
- **Z-score chart** — real-time rolling chart of the spread z-score for all pairs with ±entry and ±exit threshold lines
- **Trade history table** — all closed trades with pair, direction, entry/exit z-scores, net P&L, hold time, and close reason
- **Risk halt banner** — full-width warning with halt reason and curl command to reset
- **SSE direct connection** — bypasses the Next.js proxy (which buffers streams) and connects directly to `:8000` for zero-latency updates

### Database

- **PostgreSQL** with async SQLAlchemy — persists all trades with full entry/exit metadata
- **Auto-migration** — tables are created on first startup; no manual migration needed
- **Schema** — `PairsTrade` records pair, both symbols, direction, entry/exit z-scores, prices, quantities, hedge ratio, half-life, P&L breakdown, hold time, close reason, and post-trade balance

### Caching

- **Redis** — tick data is cached with a 30-second TTL per ticker; pub/sub channel broadcasts opportunity events

---

## Getting Started

### Prerequisites

- Python 3.9+
- Node.js 18+
- Redis (`brew install redis`)
- PostgreSQL 17 (`brew install postgresql@17`)

### Run everything

```bash
./start.sh
```

Opens the dashboard at `http://localhost:3000` and the API at `http://localhost:8000`.

```bash
./start.sh --stop    # kill all processes
./start.sh --quiet   # suppress streamed logs
```

---

## Research Workflow (before real money)

```bash
# 1. Collect live tick data — leave running for 7–30 days
./research.sh collect

# 2. Check how much data you have
./research.sh status

# 3. Grid-search optimal parameters
./research.sh optimize

# 4. Run the 14-gate go/no-go readiness report
./research.sh report

# 3+4 in one shot
./research.sh all
```

All flags are optional:

```bash
./research.sh collect  --pair BTC-USD:ETH-USD
./research.sh optimize --pair ETH-USD:SOL-USD --capital 10000
./research.sh report   --capital 2500
```

Only proceed to real money after **all 14 gates pass** and at least 2 weeks of paper trading confirm the numbers.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend runtime | Python 3.9 + asyncio |
| Exchange feeds | WebSockets (Binance, Kraken, Coinbase) |
| Strategy math | NumPy + statsmodels |
| HTTP API | FastAPI + uvicorn |
| Database | PostgreSQL 17 + async SQLAlchemy |
| Cache | Redis |
| Frontend | Next.js 14 + React 18 + Tailwind 3 + Tremor 3 |
| Alerts | Telegram Bot API |

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | No | Defaults to `postgresql+asyncpg://localhost/arbitrage` |
| `INITIAL_BALANCE` | No | Starting paper balance in USD (default: `5000`) |
| `API_PORT` | No | Backend port (default: `8000`) |
| `TELEGRAM_BOT_TOKEN` | No | Telegram bot token for alerts |
| `TELEGRAM_CHAT_ID` | No | Telegram chat ID for alerts |
| `LOG_LEVEL` | No | `DEBUG` / `INFO` / `WARNING` (default: `INFO`) |
