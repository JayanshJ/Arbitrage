"use client";

import { useSpreadStream } from "@/lib/useSpreadStream";
import { usePairsStream } from "@/lib/usePairsStream";
import StatsCards from "@/components/StatsCards";
import SpreadChart from "@/components/SpreadChart";
import LiveTickers from "@/components/LiveTickers";
import TradesTable from "@/components/TradesTable";
import BalanceChart from "@/components/BalanceChart";
import PairsStatsCards from "@/components/PairsStatsCards";
import PairsZScoreChart from "@/components/PairsZScoreChart";
import PairsTradesTable from "@/components/PairsTradesTable";

const INITIAL_BALANCE = 10_000;

export default function Dashboard() {
  const { live, history, connected } = useSpreadStream();
  const {
    live: pairsLive,
    history: pairsHistory,
    connected: pairsConnected,
  } = usePairsStream();

  const balance = live?.balance ?? INITIAL_BALANCE;
  const totalTrades = live?.total_trades ?? 0;
  const totalProfit = live?.total_profit ?? 0;

  return (
    <main className="max-w-7xl mx-auto px-4 py-8 space-y-10">

      {/* ------------------------------------------------------------------ */}
      {/* Section 1 — Cross-Exchange Arbitrage                                */}
      {/* ------------------------------------------------------------------ */}
      <section className="space-y-6">
        <header className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-white">
              Arbitrage Dashboard
            </h1>
            <p className="text-sm text-gray-400">
              Real-time crypto arbitrage paper trading
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={`inline-block h-2.5 w-2.5 rounded-full ${
                connected ? "bg-emerald-500 animate-pulse" : "bg-rose-500"
              }`}
            />
            <span className="text-sm text-gray-400">
              {connected ? "Live" : "Disconnected"}
            </span>
          </div>
        </header>

        <StatsCards
          balance={balance}
          initialBalance={INITIAL_BALANCE}
          totalTrades={totalTrades}
          totalProfit={totalProfit}
          connected={connected}
        />

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <SpreadChart data={history} />
          </div>
          <div>
            <LiveTickers spreads={live?.spreads ?? {}} />
          </div>
        </div>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <TradesTable />
          </div>
          <div>
            <BalanceChart />
          </div>
        </div>
      </section>

      {/* ------------------------------------------------------------------ */}
      {/* Divider                                                             */}
      {/* ------------------------------------------------------------------ */}
      <div className="relative flex items-center gap-4">
        <div className="flex-1 border-t border-gray-800" />
        <span className="text-xs font-semibold uppercase tracking-widest text-gray-500">
          Statistical Arbitrage
        </span>
        <div className="flex-1 border-t border-gray-800" />
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Section 2 — Statistical Arbitrage (Pairs Trading)                  */}
      {/* ------------------------------------------------------------------ */}
      <section className="space-y-6">
        <header className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-bold text-white">
              Pairs Trading
            </h2>
            <p className="text-sm text-gray-400">
              Mean-reversion on correlated crypto pairs · log-ratio z-score strategy
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={`inline-block h-2.5 w-2.5 rounded-full ${
                pairsConnected ? "bg-violet-500 animate-pulse" : "bg-rose-500"
              }`}
            />
            <span className="text-sm text-gray-400">
              {pairsConnected ? "Live" : "Disconnected"}
            </span>
          </div>
        </header>

        {/* Stats + live z-score panels */}
        <PairsStatsCards live={pairsLive} />

        {/* Z-Score chart + Trades table */}
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <PairsZScoreChart
              data={pairsHistory}
              pairs={pairsLive?.pairs ?? []}
            />
          </div>
          <div>
            <PairsTradesTable />
          </div>
        </div>
      </section>
    </main>
  );
}
