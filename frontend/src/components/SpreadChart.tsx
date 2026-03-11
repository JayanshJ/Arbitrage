"use client";

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import type { SpreadPoint } from "@/lib/useSpreadStream";

interface Props {
  data: SpreadPoint[];
}

const SYMBOLS = ["BTC-USD", "ETH-USD", "SOL-USD"];
const COLORS: Record<string, string> = {
  "BTC-USD": "#f59e0b",
  "ETH-USD": "#38bdf8",
  "SOL-USD": "#a78bfa",
};

export default function SpreadChart({ data }: Props) {
  const activeSymbols = SYMBOLS.filter((s) =>
    data.some((d) => d[s] !== undefined)
  );

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-6">
      <p className="text-sm font-medium text-gray-200 mb-4">
        Real-Time Cross-Exchange Spread (%)
      </p>
      <ResponsiveContainer width="100%" height={288}>
        <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis
            dataKey="time"
            tick={{ fill: "#9ca3af", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fill: "#9ca3af", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={64}
            tickFormatter={(v: number) => `${v.toFixed(2)}%`}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "#1f2937",
              border: "1px solid #374151",
              borderRadius: "8px",
              color: "#f3f4f6",
            }}
            formatter={(value: number) => [`${value.toFixed(4)}%`]}
          />
          <Legend
            wrapperStyle={{ color: "#d1d5db", fontSize: 12 }}
          />
          {activeSymbols.map((symbol) => (
            <Line
              key={symbol}
              type="monotone"
              dataKey={symbol}
              stroke={COLORS[symbol] ?? "#6b7280"}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
