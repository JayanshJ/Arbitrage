"use client";

import { useEffect, useRef, useState } from "react";
import type { PairsStreamData } from "./types";

const MAX_HISTORY = 120;

// Bypass Next.js proxy (it buffers streams) — connect directly to the backend.
const SSE_URL =
  typeof window !== "undefined" && window.location.hostname === "localhost"
    ? "http://localhost:8000/api/pairs/stream"
    : "/api/pairs/stream";

export interface ZScorePoint {
  time: string;
  [pairId: string]: number | string; // pair_id → z_score value
}

export function usePairsStream() {
  const [live, setLive] = useState<PairsStreamData | null>(null);
  const [history, setHistory] = useState<ZScorePoint[]>([]);
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
          const data: PairsStreamData = JSON.parse(event.data);
          setLive(data);

          // Build a z-score history point
          const now = new Date(data.timestamp * 1000);
          const timeStr = now.toLocaleTimeString("en-US", {
            hour12: false,
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          });

          const point: ZScorePoint = { time: timeStr };
          for (const pair of data.pairs) {
            if (pair.z_score !== null && pair.is_ready) {
              point[pair.pair_id] = pair.z_score;
            }
          }

          setHistory((prev) => {
            const next = [...prev, point];
            return next.length > MAX_HISTORY
              ? next.slice(next.length - MAX_HISTORY)
              : next;
          });
        } catch {
          // ignore parse errors
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
