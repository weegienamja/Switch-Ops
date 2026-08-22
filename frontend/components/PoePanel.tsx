"use client";
import type { PoeResponse } from "@/lib/types";
import { formatWatts } from "@/lib/format";

export default function PoePanel({ poe }: { poe: PoeResponse }) {
  const pct = poe.availableWatts
    ? Math.min(100, (poe.usedWatts / poe.availableWatts) * 100)
    : 0;
  return (
    <div className="card">
      <div className="card__head">
        <h3 className="card__title">Power over Ethernet</h3>
        <span className="badge">
          {formatWatts(poe.usedWatts)} / {formatWatts(poe.availableWatts)}
        </span>
      </div>
      <div className={`bar ${pct > 80 ? "bar--amber" : ""}`} style={{ marginBottom: 14 }}>
        <div className="bar__fill" style={{ ["--pct" as any]: `${pct}%` }} />
      </div>
      <table className="table table--mono">
        <thead>
          <tr>
            <th>Port</th>
            <th>Admin</th>
            <th>Oper</th>
            <th>Power</th>
            <th>Class</th>
            <th>Device</th>
          </tr>
        </thead>
        <tbody>
          {poe.ports.map((p) => (
            <tr key={p.interface}>
              <td>{p.interface}</td>
              <td>{p.admin}</td>
              <td>
                <span
                  className={`badge ${
                    p.oper === "on" ? "badge--green" : ""
                  }`}
                >
                  {p.oper}
                </span>
              </td>
              <td>{formatWatts(p.powerWatts)}</td>
              <td>{p.class}</td>
              <td>{p.device || "—"}</td>
            </tr>
          ))}
          {poe.ports.length === 0 ? (
            <tr>
              <td colSpan={6} style={{ color: "var(--text-dim)", textAlign: "center" }}>
                PoE telemetry unavailable
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}
