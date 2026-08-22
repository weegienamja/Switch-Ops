"use client";
import type { LogsResponse } from "@/lib/types";

export default function LogsPanel({ logs }: { logs: LogsResponse }) {
  return (
    <div className="card">
      <div className="card__head">
        <h3 className="card__title">Recent log entries</h3>
        <span className="badge">{logs.entries.length} lines</span>
      </div>
      <div className="pre" style={{ maxHeight: 320 }}>
        {logs.entries.map((e, i) => (
          <div key={i} className={`severity-${e.severity}`}>
            {e.line}
          </div>
        ))}
        {logs.entries.length === 0 && (
          <div style={{ color: "var(--text-dim)" }}>no log entries</div>
        )}
      </div>
    </div>
  );
}
