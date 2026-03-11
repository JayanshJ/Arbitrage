"use client";

import { useEffect, useRef, useState } from "react";
import type { StreamData } from "./types";

const MAX_HISTORY = 120;
// SSE must bypass Next.js proxy (it buffers streams), connect directly to backend
const SSE_URL =
  typeof window !== "undefined" && window.location.hostname === "localhost"
    ? "http://localhost:8000/api/spreads/stream"
    : "/api/spreads/stream";

export interface SpreadPoint {
  time: string;
  [symbol: string]: number | string;
}

export function useSpreadStream() {
  const [live, setLive] = useState<StreamData | null>(null);
  const [history, setHistory] = useState<SpreadPoint[]>([]);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;

    function connect() {
      if (!mountedRef.current) return;
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }

      const es = new EventSource(SSE_URL);
      esRef.current = es;

      es.onopen = () => {
        if (mountedRef.current) setConnected(true);
      };

      es.onmessage = (event) => {
        if (!mountedRef.current) return;
        try {
          const data: StreamData = JSON.parse(event.data);
          setLive(data);

          const now = new Date(data.timestamp * 1000);
          const timeStr = now.toLocaleTimeString("en-US", {
            hour12: false,
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          });

          const point: SpreadPoint = { time: timeStr };
          for (const [symbol, spread] of Object.entries(data.spreads)) {
            point[symbol] = spread.spread_pct;
          }

          setHistory((prev) => {
            const next = [...prev, point];
            return next.length > MAX_HISTORY
              ? next.slice(next.length - MAX_HISTORY)
              : next;
          });
        } catch {
          // ignore
        }
      };

      es.onerror = () => {
        if (!mountedRef.current) return;
        setConnected(false);
        es.close();
        esRef.current = null;
        timerRef.current = setTimeout(connect, 2000);
      };
    }

    connect();

    return () => {
      mountedRef.current = false;
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
    };
  }, []);

  return { live, history, connected };
}
