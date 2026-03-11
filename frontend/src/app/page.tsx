"use client";

import { useSpreadStream } from "@/lib/useSpreadStream";
import StatsCards from "@/components/StatsCards";
import SpreadChart from "@/components/SpreadChart";
import LiveTickers from "@/components/LiveTickers";
import TradesTable from "@/components/TradesTable";
import BalanceChart from "@/components/BalanceChart";

const INITIAL_BALANCE = 10_000;

export default function Dashboard() {
  const { live, history, connected } = useSpreadStream();

  const balance = live?.balance ?? INITIAL_BALANCE;
  const totalTrades = live?.total_trades ?? 0;
  const totalProfit = live?.total_profit ?? 0;

  return (
    <main className="max-w-7xl mx-auto px-4 py-8 space-y-6">
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
    </main>
  );
}
