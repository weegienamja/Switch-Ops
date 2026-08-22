"use client";

import { useEffect, useState } from "react";
import type { LiveConnection, LiveFreshness } from "@/lib/types";

function ageLabel(value?: string | null, now = Date.now()): string {
  if (!value) return "waiting";
  const seconds = Math.max(0, Math.round((now - new Date(value).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m`;
}

export function visibleConnectionState(
  connection: LiveConnection,
  streamState: "connecting" | "open" | "error" | "unavailable",
): "LIVE" | "STALE" | "RECONNECTING" | "OFFLINE" {
  if (streamState === "error" || streamState === "connecting") return "RECONNECTING";
  if (connection.state === "live") return "LIVE";
  if (connection.state === "stale") return "STALE";
  if (connection.state === "connecting" || connection.state === "reconnecting") {
    return "RECONNECTING";
  }
  return "OFFLINE";
}

export default function LiveStatusBadge({
  connection,
  streamState,
  freshness,
}: {
  connection: LiveConnection;
  streamState: "connecting" | "open" | "error" | "unavailable";
  freshness: LiveFreshness;
}) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 5000);
    return () => window.clearInterval(timer);
  }, []);
  const state = visibleConnectionState(connection, streamState);
  const tone =
    state === "LIVE"
      ? "live-status--live"
      : state === "STALE"
        ? "live-status--stale"
        : state === "RECONNECTING"
          ? "live-status--reconnecting"
          : "live-status--offline";
  return (
    <div className="live-status" title={connection.error || "Persistent local device session"}>
      <span className={`badge live-status__badge ${tone}`}>
        <i aria-hidden /> {state}
      </span>
      <span className="live-status__freshness" aria-label="Telemetry freshness">
        fast {ageLabel(freshness.fast, now)} · medium {ageLabel(freshness.medium, now)} · slow{" "}
        {ageLabel(freshness.slow, now)}
      </span>
    </div>
  );
}
