"use client";

import { useEffect, useState } from "react";
import {
  Card,
  Title,
  Table,
  TableHead,
  TableHeaderCell,
  TableBody,
  TableRow,
  TableCell,
  Badge,
  Text,
} from "@tremor/react";
import { fetchTrades } from "@/lib/api";
import type { Trade } from "@/lib/types";

export default function TradesTable() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  const loadTrades = async () => {
    try {
      const data = await fetchTrades(20);
      setTrades(data.trades);
      setTotal(data.total);
    } catch {
      // API not available yet
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTrades();
    const interval = setInterval(loadTrades, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <Card>
      <div className="flex items-center justify-between">
        <Title>Paper Trade History</Title>
        <Text>{total} total trades</Text>
      </div>

      {loading ? (
        <p className="text-gray-400 mt-4 text-sm">Loading...</p>
      ) : trades.length === 0 ? (
        <p className="text-gray-400 mt-4 text-sm">
          No trades yet. Waiting for arbitrage opportunities...
        </p>
      ) : (
        <Table className="mt-4">
          <TableHead>
            <TableRow>
              <TableHeaderCell>Time</TableHeaderCell>
              <TableHeaderCell>Symbol</TableHeaderCell>
              <TableHeaderCell>Buy</TableHeaderCell>
              <TableHeaderCell>Sell</TableHeaderCell>
              <TableHeaderCell>Qty</TableHeaderCell>
              <TableHeaderCell>Net P&L</TableHeaderCell>
              <TableHeaderCell>Balance</TableHeaderCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {trades.map((t) => (
              <TableRow key={t.id}>
                <TableCell className="text-xs">
                  {t.created_at
                    ? new Date(t.created_at).toLocaleTimeString()
                    : "-"}
                </TableCell>
                <TableCell className="font-medium">{t.symbol}</TableCell>
                <TableCell>
                  <span className="text-xs text-gray-400">{t.buy_exchange}</span>
                  <br />${t.buy_price.toLocaleString("en-US", { maximumFractionDigits: 2 })}
                </TableCell>
                <TableCell>
                  <span className="text-xs text-gray-400">{t.sell_exchange}</span>
                  <br />${t.sell_price.toLocaleString("en-US", { maximumFractionDigits: 2 })}
                </TableCell>
                <TableCell>{t.quantity.toFixed(6)}</TableCell>
                <TableCell>
                  <Badge
                    color={t.net_profit >= 0 ? "emerald" : "rose"}
                    size="sm"
                  >
                    ${t.net_profit.toFixed(2)} ({t.net_profit_pct.toFixed(2)}%)
                  </Badge>
                </TableCell>
                <TableCell>
                  ${t.balance_after.toLocaleString("en-US", { maximumFractionDigits: 2 })}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Card>
  );
}
