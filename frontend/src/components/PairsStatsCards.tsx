"use client";

import type { PairsStreamData, PairStatus } from "@/lib/types";

interface Props {
  live: PairsStreamData | null;
}

function Card({
  title,
  value,
  sub,
  valueColor,
}: {
  title: string;
  value: string;
  sub?: string;
  valueColor?: string;
}) {
  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-5">
      <p className="text-xs font-medium uppercase tracking-wider text-gray-500">
        {title}
      </p>
      <p
        className={`mt-1.5 text-2xl font-bold tabular-nums ${
          valueColor ?? "text-white"
        }`}
      >
        {value}
      </p>
      {sub && <p className="mt-1 text-xs text-gray-500">{sub}</p>}
    </div>
  );
}

function CorrelationPill({ pair }: { pair: PairStatus }) {
  const corr = pair.correlation;
  if (corr === null) return null;
  const color =
    corr > 0.8
      ? "text-emerald-400 bg-emerald-900/30"
      : corr > 0.5
      ? "text-yellow-400 bg-yellow-900/30"
      : "text-red-400 bg-red-900/30";
  return (
    <span className={`rounded px-1.5 py-0.5 text-xs font-mono ${color}`}>
      ρ={corr.toFixed(2)}
    </span>
  );
}

export default function PairsStatsCards({ live }: Props) {
  const balance = live?.pairs_balance ?? 5_000;
  const initial = live?.pairs_initial_balance ?? 5_000;
  const profit = live?.pairs_total_profit ?? 0;
  const trades = live?.pairs_total_trades ?? 0;
  const pairs = live?.pairs ?? [];

  const pnlPct = initial > 0 ? ((balance - initial) / initial) * 100 : 0;
  const openPositions = pairs.filter((p) => p.has_position).length;

  const profitColor =
    profit > 0 ? "text-emerald-400" : profit < 0 ? "text-rose-400" : "text-white";

  return (
    <div className="space-y-4">
      {/* Stat row */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Card
          title="Pairs Balance"
          value={`$${balance.toLocaleString("en-US", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
          })}`}
          sub={`Started $${initial.toLocaleString()}`}
        />
        <Card
          title="Pairs P&L"
          value={`${profit >= 0 ? "+" : ""}$${profit.toLocaleString("en-US", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
          })}`}
          sub={`${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(2)}%`}
          valueColor={profitColor}
        />
        <Card
          title="Pairs Trades"
          value={trades.toString()}
          sub="closed round-trips"
        />
        <Card
          title="Open Positions"
          value={openPositions.toString()}
          sub={`of ${pairs.length} pairs`}
          valueColor={openPositions > 0 ? "text-amber-400" : "text-white"}
        />
      </div>

      {/* Live pair status row */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {pairs.map((pair) => {
          const zAbs = pair.z_score !== null ? Math.abs(pair.z_score) : null;
          const zColor =
            zAbs === null
              ? "text-gray-400"
              : zAbs >= 2
              ? "text-red-400"
              : zAbs >= 0.5
              ? "text-yellow-400"
              : "text-emerald-400";

          return (
            <div
              key={pair.pair_id}
              className="flex items-center justify-between rounded-lg border border-gray-800 bg-gray-900 px-4 py-3"
            >
              <div className="flex items-center gap-3">
                <div
                  className={`h-2.5 w-2.5 rounded-full ${
                    pair.has_position
                      ? "bg-amber-400 animate-pulse"
                      : pair.is_ready
                      ? "bg-emerald-500"
                      : "bg-gray-600"
                  }`}
                />
                <div>
                  <p className="text-sm font-medium text-gray-200">
                    {pair.pair_id}
                  </p>
                  <div className="flex items-center gap-2 mt-0.5">
                    <CorrelationPill pair={pair} />
                    {pair.data_points < pair.window && (
                      <span className="text-xs text-gray-500">
                        {pair.data_points}/{pair.window} ticks
                      </span>
                    )}
                    {pair.has_position && pair.position && (
                      <span
                        className={`text-xs font-medium ${
                          (pair.position.unrealized_pnl ?? 0) >= 0
                            ? "text-emerald-400"
                            : "text-rose-400"
                        }`}
                      >
                        P&L:{" "}
                        {pair.position.unrealized_pnl !== null
                          ? `${pair.position.unrealized_pnl >= 0 ? "+" : ""}$${pair.position.unrealized_pnl.toFixed(2)}`
                          : "—"}
                      </span>
                    )}
                  </div>
                </div>
              </div>

              <div className="text-right">
                <p className={`text-lg font-bold font-mono tabular-nums ${zColor}`}>
                  {pair.z_score !== null
                    ? `${pair.z_score >= 0 ? "+" : ""}${pair.z_score.toFixed(2)}σ`
                    : "—"}
                </p>
                <p className="text-xs text-gray-500 mt-0.5">
                  {pair.price_a > 0
                    ? `${pair.symbol_a} $${pair.price_a.toLocaleString("en-US", {
                        maximumFractionDigits: 0,
                      })}`
                    : ""}
                </p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
