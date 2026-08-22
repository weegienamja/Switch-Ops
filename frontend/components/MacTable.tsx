"use client";
import { useMemo, useState } from "react";
import type { MacTableResponse } from "@/lib/types";

export default function MacTable({ mac }: { mac: MacTableResponse }) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => {
    if (!query.trim()) return mac.entries;
    const q = query.toLowerCase();
    return mac.entries.filter(
      (e) =>
        e.mac.toLowerCase().includes(q) ||
        e.port.toLowerCase().includes(q) ||
        e.vlan.includes(q),
    );
  }, [mac.entries, query]);
  return (
    <div className="card">
      <div className="card__head">
        <h3 className="card__title">MAC address table</h3>
        <input
          className="input"
          placeholder="filter mac / port / vlan"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ maxWidth: 240 }}
        />
      </div>
      <div style={{ maxHeight: 320, overflow: "auto" }}>
        <table className="table table--mono">
          <thead>
            <tr>
              <th>VLAN</th>
              <th>MAC</th>
              <th>Type</th>
              <th>Port</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((e, i) => (
              <tr key={`${e.mac}-${e.port}-${i}`}>
                <td>{e.vlan}</td>
                <td>{e.mac}</td>
                <td>{e.type}</td>
                <td>{e.port}</td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={4} style={{ color: "var(--text-dim)", textAlign: "center" }}>
                  no entries
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
