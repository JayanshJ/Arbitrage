import type { PairsTrade } from "./types";

const API_BASE = "/api";

export async function fetchPairsTrades(
  limit = 50,
  offset = 0,
): Promise<{ total: number; trades: PairsTrade[] }> {
  const res = await fetch(
    `${API_BASE}/pairs/trades?limit=${limit}&offset=${offset}`,
  );
  if (!res.ok) throw new Error(`Failed to fetch pairs trades: ${res.status}`);
  return res.json();
}
