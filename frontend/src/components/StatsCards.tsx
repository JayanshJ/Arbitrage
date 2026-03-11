"use client";

import { Card, Metric, Text, Flex, BadgeDelta } from "@tremor/react";

interface Props {
  balance: number;
  initialBalance: number;
  totalTrades: number;
  totalProfit: number;
  connected: boolean;
}

export default function StatsCards({
  balance,
  initialBalance,
  totalTrades,
  totalProfit,
  connected,
}: Props) {
  const pnlPct =
    initialBalance > 0
      ? ((balance - initialBalance) / initialBalance) * 100
      : 0;

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Card decoration="top" decorationColor="blue">
        <Text>Balance</Text>
        <Metric>${balance.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</Metric>
      </Card>

      <Card decoration="top" decorationColor={totalProfit >= 0 ? "emerald" : "rose"}>
        <Flex justifyContent="between" alignItems="center">
          <div>
            <Text>Total P&L</Text>
            <Metric>
              ${totalProfit.toLocaleString("en-US", {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
                signDisplay: "always",
              })}
            </Metric>
          </div>
          <BadgeDelta
            deltaType={pnlPct >= 0 ? "increase" : "decrease"}
            size="lg"
          >
            {pnlPct.toFixed(2)}%
          </BadgeDelta>
        </Flex>
      </Card>

      <Card decoration="top" decorationColor="violet">
        <Text>Paper Trades</Text>
        <Metric>{totalTrades}</Metric>
      </Card>

      <Card decoration="top" decorationColor={connected ? "emerald" : "rose"}>
        <Text>Connection</Text>
        <Metric className="text-lg">
          {connected ? "Live" : "Reconnecting..."}
        </Metric>
      </Card>
    </div>
  );
}
