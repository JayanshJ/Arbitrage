"use client";

import { useEffect, useState } from "react";
import { Card, Title, AreaChart } from "@tremor/react";
import { fetchBalances } from "@/lib/api";
import type { BalanceSnapshot } from "@/lib/types";

export default function BalanceChart() {
  const [data, setData] = useState<{ time: string; Balance: number }[]>([]);

  const loadBalances = async () => {
    try {
      const { balances } = await fetchBalances();
      setData(
        balances.map((b: BalanceSnapshot) => ({
          time: b.created_at
            ? new Date(b.created_at).toLocaleTimeString("en-US", {
                hour12: false,
              })
            : "",
          Balance: b.balance,
        }))
      );
    } catch {
      // API not available yet
    }
  };

  useEffect(() => {
    loadBalances();
    const interval = setInterval(loadBalances, 10000);
    return () => clearInterval(interval);
  }, []);

  return (
    <Card>
      <Title>Balance Over Time</Title>
      {data.length <= 1 ? (
        <p className="text-gray-400 mt-4 text-sm">
          Balance history will appear after trades are executed.
        </p>
      ) : (
        <AreaChart
          className="h-48 mt-4"
          data={data}
          index="time"
          categories={["Balance"]}
          colors={["emerald"]}
          yAxisWidth={80}
          valueFormatter={(v: number) =>
            `$${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}`
          }
          showAnimation={false}
          curveType="monotone"
        />
      )}
    </Card>
  );
}
