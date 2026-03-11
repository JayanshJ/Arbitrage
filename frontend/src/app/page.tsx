"use client";

import { usePairsStream } from "@/lib/usePairsStream";
import PairsStatsCards from "@/components/PairsStatsCards";
import PairsZScoreChart from "@/components/PairsZScoreChart";
import PairsTradesTable from "@/components/PairsTradesTable";

export default function Dashboard() {
  const { live, history, connected } = usePairsStream();

  const risk = live?.risk;
  const isHalted = risk?.halted ?? false;

  return (
    <main className="max-w-7xl mx-auto px-4 py-8 space-y-6">

      {/* ── Header ──────────────────────────────────────────────────── */}
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">
            Pairs Trading Dashboard
          </h1>
          <p className="text-sm text-gray-400 mt-0.5">
            Statistical arbitrage · mean-reversion on cointegrated crypto pairs
          </p>
        </div>

        {/* Connection pill */}
        <div className="flex items-center gap-2">
          <span
            className={`inline-block h-2.5 w-2.5 rounded-full ${
              connected ? "bg-violet-500 animate-pulse" : "bg-rose-500"
            }`}
          />
          <span className="text-sm text-gray-400">
            {connected ? "Live" : "Disconnected"}
          </span>
        </div>
      </header>

      {/* ── Risk halt banner ─────────────────────────────────────────── */}
      {isHalted && (
        <div className="flex items-start gap-3 rounded-xl border border-rose-800 bg-rose-950/50 px-5 py-4">
          <span className="mt-0.5 text-xl">⛔</span>
          <div>
            <p className="font-semibold text-rose-300">Trading Halted</p>
            <p className="text-sm text-rose-400 mt-0.5">
              {risk?.halt_reason ?? "Unknown reason"}
            </p>
            <p className="text-xs text-rose-600 mt-2">
              Investigate the cause, then reset via:{" "}
              <code className="bg-rose-900/50 px-1 py-0.5 rounded text-rose-300">
                curl -X POST http://localhost:8000/api/risk/reset-halt
              </code>
            </p>
          </div>
        </div>
      )}

      {/* ── Stats + live pair panels ─────────────────────────────────── */}
      <PairsStatsCards live={live} />

      {/* ── Z-score chart ────────────────────────────────────────────── */}
      <PairsZScoreChart
        data={history}
        pairs={live?.pairs ?? []}
      />

      {/* ── Trade history ────────────────────────────────────────────── */}
      <PairsTradesTable />

    </main>
  );
}
