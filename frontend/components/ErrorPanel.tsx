"use client";
import type { InterfaceErrorsResponse } from "@/lib/types";

export default function ErrorPanel({
  errors,
}: {
  errors: InterfaceErrorsResponse;
}) {
  return (
    <div className="card">
      <div className="card__head">
        <h3 className="card__title">Interface error counters</h3>
        <span
          className={`badge ${
            errors.healthy ? "badge--green" : "badge--amber"
          }`}
        >
          {errors.totalErrors} total
        </span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="table table--mono">
          <thead>
            <tr>
              <th>Port</th>
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
                <td colSpan={7} style={{ color: "var(--text-dim)", textAlign: "center" }}>
                  error-counter telemetry unavailable
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
