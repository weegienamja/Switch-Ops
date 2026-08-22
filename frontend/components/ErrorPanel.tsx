"use client";
import type { InterfaceErrorsResponse, TelemetrySnapshotSummary } from "@/lib/types";

export default function ErrorPanel({
  errors,
  telemetry,
}: {
  errors: InterfaceErrorsResponse;
  telemetry: TelemetrySnapshotSummary;
}) {
  const deltaByPort = new Map(telemetry.interfaceDeltas.map((delta) => [delta.port, delta]));
  const newErrors = telemetry.interfaceDeltas.reduce(
    (total, delta) => total + Math.max(0, delta.errorDelta || 0),
    0,
  );
  return (
    <div className="card">
      <div className="card__head">
        <h3 className="card__title">Interface error counters</h3>
        <span
          className={`badge ${
            errors.healthy ? "badge--green" : "badge--amber"
          }`}
        >
          {telemetry.historyAvailable ? `+${newErrors} since previous` : "baseline captured"}
        </span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="table table--mono">
          <thead>
            <tr>
              <th>Port</th>
              <th>Change</th>
              <th>Align</th>
              <th>FCS</th>
              <th>Xmit</th>
              <th>Rcv</th>
              <th>Late col</th>
              <th>Excess</th>
            </tr>
          </thead>
          <tbody>
            {errors.counters.map((c) => (
              <tr key={c.port}>
                <td>{c.port}</td>
                <td>
                  {deltaByPort.get(c.port)?.errorDelta == null
                    ? deltaByPort.get(c.port)?.counterState || "—"
                    : `+${deltaByPort.get(c.port)?.errorDelta}`}
                </td>
                <td>{c.alignErr}</td>
                <td>{c.fcsErr}</td>
                <td>{c.xmitErr}</td>
                <td>{c.rcvErr}</td>
                <td>{c.lateCol}</td>
                <td>{c.excessCol}</td>
              </tr>
            ))}
            {errors.counters.length === 0 ? (
              <tr>
                <td colSpan={8} style={{ color: "var(--text-dim)", textAlign: "center" }}>
                  error-counter telemetry unavailable
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      <p className="empty-note" style={{ marginBottom: 0 }}>
        Totals are cumulative. Current health uses the change since the previous observation, not whether a historic total is non-zero.
      </p>
    </div>
  );
}
