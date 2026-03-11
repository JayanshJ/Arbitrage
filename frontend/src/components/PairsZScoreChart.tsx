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
  ReferenceLine,
} from "recharts";
import type { ZScorePoint } from "@/lib/usePairsStream";
import type { PairStatus } from "@/lib/types";

interface Props {
  data: ZScorePoint[];
  pairs: PairStatus[];
}

const PAIR_COLORS: Record<string, string> = {
  "ETH-USD:SOL-USD": "#a78bfa",  // violet
  "BTC-USD:ETH-USD": "#fb923c",  // orange
};

const SIGNAL_LABELS: Record<string, { label: string; color: string }> = {
  long_a_short_b: { label: "Long A / Short B", color: "#34d399" },
  short_a_long_b: { label: "Short A / Long B", color: "#f87171" },
  close: { label: "Close", color: "#fbbf24" },
  none: { label: "Holding", color: "#6b7280" },
  warming_up: { label: "Warming up…", color: "#6b7280" },
};

function SignalBadge({ signal }: { signal: string }) {
  const { label, color } = SIGNAL_LABELS[signal] ?? {
    label: signal,
    color: "#6b7280",
  };
  return (
    <span
      className="inline-flex items-center rounded px-2 py-0.5 text-xs font-medium"
      style={{ backgroundColor: `${color}22`, color }}
    >
      {label}
    </span>
  );
}

export default function PairsZScoreChart({ data, pairs }: Props) {
  const activePairs = Object.keys(PAIR_COLORS).filter((p) =>
    data.some((d) => d[p] !== undefined)
  );

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-6">
      {/* Header */}
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-gray-200">
            Z-Score — Statistical Spread
          </p>
          <p className="text-xs text-gray-500 mt-0.5">
            Entry ±2σ · Exit ±0.5σ · Window: 60 ticks
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {pairs.map((p) => (
            <div
              key={p.pair_id}
              className="flex items-center gap-1.5 rounded-lg border border-gray-700 bg-gray-800 px-2 py-1"
            >
              <span
                className="h-2 w-2 rounded-full"
                style={{ backgroundColor: PAIR_COLORS[p.pair_id] ?? "#6b7280" }}
              />
              <span className="text-xs text-gray-300">{p.pair_id}</span>
              <SignalBadge signal={p.signal} />
              {p.z_score !== null && (
                <span
                  className="ml-1 text-xs font-mono font-semibold"
                  style={{
                    color:
                      Math.abs(p.z_score) >= 2
                        ? "#ef4444"
                        : Math.abs(p.z_score) >= 0.5
                        ? "#fbbf24"
                        : "#34d399",
                  }}
                >
                  {p.z_score > 0 ? "+" : ""}
                  {p.z_score.toFixed(2)}σ
                </span>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Chart */}
      <ResponsiveContainer width="100%" height={288}>
        <LineChart
          data={data}
          margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />

          {/* Zero line */}
          <ReferenceLine y={0} stroke="#4b5563" strokeWidth={1} />

          {/* Exit thresholds */}
          <ReferenceLine
            y={0.5}
            stroke="#eab308"
            strokeDasharray="3 3"
            strokeWidth={1}
          />
          <ReferenceLine
            y={-0.5}
            stroke="#eab308"
            strokeDasharray="3 3"
            strokeWidth={1}
          />

          {/* Entry thresholds */}
          <ReferenceLine
            y={2}
            stroke="#ef4444"
            strokeDasharray="5 3"
            strokeWidth={1.5}
            label={{
              value: "+2σ entry",
              position: "insideTopRight",
              fill: "#ef4444",
              fontSize: 10,
            }}
          />
          <ReferenceLine
            y={-2}
            stroke="#ef4444"
            strokeDasharray="5 3"
            strokeWidth={1.5}
            label={{
              value: "-2σ entry",
              position: "insideBottomRight",
              fill: "#ef4444",
              fontSize: 10,
            }}
          />

          <XAxis
            dataKey="time"
            tick={{ fill: "#9ca3af", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            domain={[-4, 4]}
            tick={{ fill: "#9ca3af", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={40}
            tickFormatter={(v: number) => `${v.toFixed(1)}σ`}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "#1f2937",
              border: "1px solid #374151",
              borderRadius: "8px",
              color: "#f3f4f6",
              fontSize: 12,
            }}
            formatter={(value: number, name: string) => [
              `${value > 0 ? "+" : ""}${value.toFixed(3)}σ`,
              name,
            ]}
          />
          <Legend wrapperStyle={{ color: "#d1d5db", fontSize: 12 }} />

          {activePairs.map((pairId) => (
            <Line
              key={pairId}
              type="monotone"
              dataKey={pairId}
              stroke={PAIR_COLORS[pairId] ?? "#6b7280"}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>

      {/* Legend for reference lines */}
      <div className="mt-3 flex items-center gap-4 text-xs text-gray-500">
        <span className="flex items-center gap-1">
          <span className="inline-block h-px w-6 border-t-2 border-dashed border-red-500" />
          Entry ±2σ
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-px w-6 border-t border-dashed border-yellow-500" />
          Exit ±0.5σ
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-px w-6 border-t border-gray-600" />
          Mean (0)
        </span>
      </div>
    </div>
  );
}
