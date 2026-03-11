"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchPairsTrades } from "@/lib/api";
import type { PairsTrade } from "@/lib/types";

const PAGE_SIZE = 20;
const REFRESH_MS = 8_000;

function DirectionBadge({ direction }: { direction: string }) {
  const isLongAShortB = direction === "long_a_short_b";
  return (
    <span
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ${
        isLongAShortB
          ? "bg-emerald-900/40 text-emerald-400"
          : "bg-rose-900/40 text-rose-400"
      }`}
    >
      {isLongAShortB ? "Long A / Short B" : "Short A / Long B"}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium ${
        status === "open"
          ? "bg-amber-900/40 text-amber-400"
          : "bg-gray-800 text-gray-400"
      }`}
    >
      {status === "open" && (
        <span className="h-1.5 w-1.5 rounded-full bg-amber-400 animate-pulse" />
      )}
      {status}
    </span>
  );
}

const CLOSE_REASON_STYLES: Record<string, string> = {
  exit_signal:       "bg-emerald-900/40 text-emerald-400",
  stop_loss:         "bg-rose-900/40 text-rose-400",
  max_hold:          "bg-orange-900/40 text-orange-400",
  revalidation_fail: "bg-violet-900/40 text-violet-400",
};

function CloseReasonBadge({ reason }: { reason: string | null }) {
  if (!reason) return <span className="text-gray-600">—</span>;
  const style = CLOSE_REASON_STYLES[reason] ?? "bg-gray-800 text-gray-400";
  const label = reason.replace(/_/g, " ");
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ${style}`}>
      {label}
    </span>
  );
}

function fmt(n: number | null, decimals = 2) {
  if (n === null || n === undefined) return "—";
  const s = Math.abs(n).toFixed(decimals);
  return `${n >= 0 ? "+" : "-"}${s}`;
}

function fmtSeconds(s: number | null) {
  if (s === null) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}

function fmtTime(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-US", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export default function PairsTradesTable() {
  const [trades, setTrades] = useState<PairsTrade[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);

  const load = useCallback(
    async (off: number) => {
      try {
        const data = await fetchPairsTrades(PAGE_SIZE, off);
        setTrades(data.trades);
        setTotal(data.total);
      } catch {
        // ignore network errors
      } finally {
        setLoading(false);
      }
    },
    []
  );

  // Auto-refresh
  useEffect(() => {
    load(offset);
    const timer = setInterval(() => load(offset), REFRESH_MS);
    return () => clearInterval(timer);
  }, [load, offset]);

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <p className="text-sm font-medium text-gray-200">
            Pairs Trade History
          </p>
          <p className="text-xs text-gray-500 mt-0.5">
            {total} total pairs trades
          </p>
        </div>
        {totalPages > 1 && (
          <div className="flex items-center gap-2 text-xs text-gray-400">
            <button
              className="rounded px-2 py-1 hover:bg-gray-800 disabled:opacity-40"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              ← Prev
            </button>
            <span>
              {currentPage}/{totalPages}
            </span>
            <button
              className="rounded px-2 py-1 hover:bg-gray-800 disabled:opacity-40"
              disabled={offset + PAGE_SIZE >= total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next →
            </button>
          </div>
        )}
      </div>

      {loading ? (
        <div className="flex h-40 items-center justify-center text-sm text-gray-500">
          Loading…
        </div>
      ) : trades.length === 0 ? (
        <div className="flex h-40 flex-col items-center justify-center gap-2 text-center">
          <p className="text-sm text-gray-400">No pairs trades yet</p>
          <p className="text-xs text-gray-600">
            The engine needs ~30 s to warm up the rolling window, then it will
            start trading when |z-score| &gt; 2σ.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="pb-2 pr-3 font-medium">Time</th>
                <th className="pb-2 pr-3 font-medium">Pair</th>
                <th className="pb-2 pr-3 font-medium">Direction</th>
                <th className="pb-2 pr-3 font-medium text-right">Entry z</th>
                <th className="pb-2 pr-3 font-medium text-right">Exit z</th>
                <th className="pb-2 pr-3 font-medium text-right" title="OLS hedge ratio β">β</th>
                <th className="pb-2 pr-3 font-medium text-right" title="OU half-life in hours">HL (h)</th>
                <th className="pb-2 pr-3 font-medium text-right">Hold</th>
                <th className="pb-2 pr-3 font-medium text-right">Net P&L</th>
                <th className="pb-2 pr-3 font-medium">Close reason</th>
                <th className="pb-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/60">
              {trades.map((t) => {
                const pnlColor =
                  t.net_pnl === null
                    ? "text-gray-400"
                    : t.net_pnl > 0
                    ? "text-emerald-400"
                    : t.net_pnl < 0
                    ? "text-rose-400"
                    : "text-gray-400";

                return (
                  <tr
                    key={t.id}
                    className="group transition-colors hover:bg-gray-800/30"
                  >
                    <td className="py-2 pr-3 font-mono text-gray-400">
                      {fmtTime(t.created_at)}
                    </td>
                    <td className="py-2 pr-3">
                      <span className="font-medium text-gray-200">
                        {t.pair_id}
                      </span>
                    </td>
                    <td className="py-2 pr-3">
                      <DirectionBadge direction={t.direction} />
                    </td>
                    <td className="py-2 pr-3 text-right font-mono text-gray-300">
                      {t.entry_z_score > 0 ? "+" : ""}
                      {t.entry_z_score.toFixed(2)}σ
                    </td>
                    <td className="py-2 pr-3 text-right font-mono text-gray-400">
                      {t.exit_z_score !== null
                        ? `${t.exit_z_score > 0 ? "+" : ""}${t.exit_z_score.toFixed(2)}σ`
                        : "—"}
                    </td>
                    <td className="py-2 pr-3 text-right font-mono text-gray-400">
                      {t.hedge_ratio !== null ? t.hedge_ratio.toFixed(3) : "—"}
                    </td>
                    <td className="py-2 pr-3 text-right font-mono text-gray-400">
                      {t.half_life_hours !== null ? t.half_life_hours.toFixed(1) : "—"}
                    </td>
                    <td className="py-2 pr-3 text-right text-gray-400">
                      {fmtSeconds(t.hold_seconds)}
                    </td>
                    <td className={`py-2 pr-3 text-right font-mono font-semibold ${pnlColor}`}>
                      {t.net_pnl !== null
                        ? `${t.net_pnl >= 0 ? "+" : ""}$${Math.abs(t.net_pnl).toFixed(2)}`
                        : "—"}
                    </td>
                    <td className="py-2 pr-3">
                      <CloseReasonBadge reason={t.close_reason ?? null} />
                    </td>
                    <td className="py-2">
                      <StatusBadge status={t.status} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
