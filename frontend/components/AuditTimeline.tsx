"use client";
import type { AuditEvent } from "@/lib/types";

export default function AuditTimeline({ events }: { events: AuditEvent[] }) {
  return (
    <div className="card">
      <div className="card__head">
        <h3 className="card__title">Audit timeline</h3>
        <span className="badge">{events.length} events</span>
      </div>
      {events.length === 0 ? (
        <div style={{ color: "var(--text-dim)", fontSize: 13 }}>
          No audit events yet.
        </div>
      ) : (
        <div style={{ maxHeight: 360, overflow: "auto" }}>
          <table className="table table--mono">
            <thead>
              <tr>
                <th>When</th>
                <th>Actor</th>
                <th>Action</th>
                <th>Result</th>
                <th>Duration</th>
                <th>Commands</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e, i) => (
                <tr key={`${e.id ?? e.timestamp}-${i}`}>
                  <td>{new Date(e.timestamp).toLocaleString()}</td>
                  <td>{e.actor}</td>
                  <td>{e.action}</td>
                  <td>
                    <span
                      className={`badge ${
                        e.success ? "badge--green" : "badge--red"
                      }`}
                    >
                      {e.success ? "ok" : "fail"}
                    </span>
                  </td>
                  <td>{e.durationMs} ms</td>
                  <td style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 320 }}>
                    {e.commands.join(" ; ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
