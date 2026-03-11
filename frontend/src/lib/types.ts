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
