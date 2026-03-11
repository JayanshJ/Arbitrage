import type { Trade, BalanceSnapshot, Stats, PairsTrade } from "./types";

const API_BASE = "/api";

export async function fetchTrades(
  limit = 50,
  offset = 0
): Promise<{ total: number; trades: Trade[] }> {
  const res = await fetch(
    `${API_BASE}/trades?limit=${limit}&offset=${offset}`
  );
  if (!res.ok) throw new Error(`Failed to fetch trades: ${res.status}`);
  return res.json();
}

export async function fetchBalances(
  limit = 200
): Promise<{ balances: BalanceSnapshot[] }> {
  const res = await fetch(`${API_BASE}/balances?limit=${limit}`);
  if (!res.ok) throw new Error(`Failed to fetch balances: ${res.status}`);
  return res.json();
}

export async function fetchStats(): Promise<Stats> {
  const res = await fetch(`${API_BASE}/stats`);
  if (!res.ok) throw new Error(`Failed to fetch stats: ${res.status}`);
  return res.json();
}

export async function fetchPairsTrades(
  limit = 50,
  offset = 0
): Promise<{ total: number; trades: PairsTrade[] }> {
  const res = await fetch(
    `${API_BASE}/pairs/trades?limit=${limit}&offset=${offset}`
  );
  if (!res.ok) throw new Error(`Failed to fetch pairs trades: ${res.status}`);
  return res.json();
}
