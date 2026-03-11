export interface Trade {
  id: number;
  symbol: string;
  quantity: number;
  buy_exchange: string;
  buy_price: number;
  buy_cost: number;
  buy_fee: number;
  sell_exchange: string;
  sell_price: number;
  sell_revenue: number;
  sell_fee: number;
  slippage_cost: number;
  gross_profit: number;
  net_profit: number;
  net_profit_pct: number;
  balance_after: number;
  status: string;
  created_at: string;
}

export interface BalanceSnapshot {
  id: number;
  balance: number;
  trade_id: number | null;
  reason: string;
  created_at: string;
}

export interface TickerInfo {
  bid: number;
  ask: number;
  bid_qty: number;
  ask_qty: number;
  spread_bps: number;
}

export interface SpreadInfo {
  spread_pct: number;
  buy_exchange: string;
  sell_exchange: string;
  buy_price: number;
  sell_price: number;
}

export interface StreamData {
  timestamp: number;
  spreads: Record<string, SpreadInfo>;
  balance: number;
  total_trades: number;
  total_profit: number;
}

export interface Stats {
  balance: number;
  initial_balance: number;
  total_trades: number;
  total_profit: number;
  pnl_pct: number;
  tickers: Record<string, Record<string, TickerInfo>>;
}

// ---------------------------------------------------------------------------
// Pairs / Statistical Arbitrage types
// ---------------------------------------------------------------------------

export interface PairsPosition {
  direction: string;           // "long_a_short_b" | "short_a_long_b"
  entry_z_score: number;
  entry_price_a: number;
  entry_price_b: number;
  notional_usd: number;
  unrealized_pnl: number | null;
  hold_seconds: number;
}

export interface PairStatus {
  pair_id: string;             // "ETH-USD:SOL-USD"
  symbol_a: string;
  symbol_b: string;
  is_ready: boolean;
  data_points: number;
  window: number;
  z_score: number | null;
  spread: number | null;
  mean_spread: number | null;
  std_spread: number | null;
  correlation: number | null;
  signal: string;              // "long_a_short_b" | "short_a_long_b" | "close" | "none" | "warming_up"
  price_a: number;
  price_b: number;
  has_position: boolean;
  position: PairsPosition | null;
}

export interface PairsStreamData {
  timestamp: number;
  pairs: PairStatus[];
  pairs_balance: number;
  pairs_initial_balance: number;
  pairs_total_trades: number;
  pairs_total_profit: number;
}

export interface PairsTrade {
  id: number;
  pair_id: string;
  symbol_a: string;
  symbol_b: string;
  direction: string;
  entry_z_score: number;
  entry_price_a: number;
  entry_price_b: number;
  entry_time: string;
  notional_usd: number;
  exit_z_score: number | null;
  exit_price_a: number | null;
  exit_price_b: number | null;
  exit_time: string | null;
  /** OLS hedge ratio β used for market-neutral sizing */
  hedge_ratio: number | null;
  /** Mean-reversion half-life in hours at time of entry */
  half_life_hours: number | null;
  /** Quantity traded for symbol A */
  qty_a: number | null;
  /** Quantity traded for symbol B */
  qty_b: number | null;
  pnl_a: number | null;
  pnl_b: number | null;
  net_pnl: number | null;
  hold_seconds: number | null;
  pairs_balance_after: number | null;
  /** Why the position was closed: exit_signal | stop_loss | max_hold | revalidation_fail */
  close_reason: string | null;
  status: string;              // "open" | "closed"
  created_at: string;
}
