"use client";
import type { InterfaceStatus } from "@/lib/types";

export default function PortStatusTable({
  interfaces,
}: {
  interfaces: InterfaceStatus[];
}) {
  return (
    <div className="card">
      <div className="card__head">
        <h3 className="card__title">Interface status</h3>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="table table--mono">
          <thead>
            <tr>
              <th>Port</th>
              <th>Description</th>
              <th>Status</th>
              <th>VLAN</th>
              <th>Speed</th>
              <th>Duplex</th>
              <th>Type</th>
            </tr>
          </thead>
          <tbody>
            {interfaces.map((i) => (
              <tr key={i.port}>
                <td>
                  {i.port}
                  {i.protected && (
                    <span className="badge badge--cyan" style={{ marginLeft: 8 }}>
                      protected
                    </span>
                  )}
                </td>
                <td>{i.name || <span style={{ color: "var(--text-dim)" }}>—</span>}</td>
                <td>
                  <span
                    className={`badge ${
                      i.status === "connected"
                        ? "badge--green"
                        : i.status === "notconnect"
                        ? ""
                        : i.status === "disabled"
                        ? "badge--amber"
                        : ""
                    }`}
                  >
                    {i.status}
                  </span>
                </td>
                <td>{i.vlan}</td>
                <td>{i.speed}</td>
                <td>{i.duplex}</td>
                <td>{i.type}</td>
              </tr>
            ))}
            {interfaces.length === 0 ? (
              <tr>
                <td colSpan={7} style={{ color: "var(--text-dim)", textAlign: "center" }}>
                  interface status unavailable
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
