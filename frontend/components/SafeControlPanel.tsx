"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import type { InterfaceStatus, SetupStatus } from "@/lib/types";

type Pending =
  | { kind: "enable"; port: string }
  | { kind: "disable"; port: string }
  | { kind: "poe"; port: string }
  | { kind: "description"; port: string; description: string }
  | { kind: "save" };

const WRITABLE_PORTS = new Set([
  "Gi0/3",
  "Gi0/4",
  "Gi0/5",
  "Gi0/6",
  "Gi0/7",
  "Gi0/8",
]);

export default function SafeControlPanel({
  setup,
  interfaces,
  onChange,
}: {
  setup: SetupStatus;
  interfaces: InterfaceStatus[];
  onChange: () => void;
}) {
  const writable = interfaces.filter((i) => WRITABLE_PORTS.has(i.port));
  const [port, setPort] = useState(writable[0]?.port || "");
  const [description, setDescription] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const writesEnabled = setup.enableWriteActions;

  function ask(p: Pending) {
    setError(null);
    setInfo(null);
    setPending(p);
  }

  async function confirm() {
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      if (pending.kind === "enable") await api.enablePort(pending.port);
      else if (pending.kind === "disable") await api.disablePort(pending.port);
      else if (pending.kind === "poe") await api.enablePortPoe(pending.port);
      else if (pending.kind === "description")
        await api.setPortDescription(pending.port, pending.description);
      else if (pending.kind === "save") await api.saveConfig();
      setInfo("Action applied. Configuration saved with `write memory`.");
      setPending(null);
      onChange();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <div className="card__head">
        <div>
          <h3 className="card__title">Safe controls</h3>
          <div className="card__subtitle">
            Allowlisted actions on Gi0/3–Gi0/8. Backup → change → write → verify.
          </div>
        </div>
        <span
          className={`badge ${
            writesEnabled ? "badge--green" : "badge--amber"
          }`}
        >
          {writesEnabled ? "writes enabled" : "read-only"}
        </span>
      </div>

      {!writesEnabled && (
        <div
          className="mono"
          style={{
            fontSize: 12,
            color: "var(--amber)",
            border: "1px solid rgba(245,158,11,0.3)",
            borderRadius: 8,
            padding: 12,
            marginBottom: 16,
          }}
        >
          Write actions are disabled. Set ENABLE_WRITE_ACTIONS=true in the backend
          environment to enable safe controls.
        </div>
      )}

      <div style={{ display: "grid", gap: 12 }}>
        <div>
          <label className="label">Interface</label>
          <select
            className="input"
            value={port}
            onChange={(e) => setPort(e.target.value)}
            disabled={!writesEnabled}
          >
            {writable.map((i) => (
              <option key={i.port} value={i.port}>
                {i.port} — {i.name || "no description"}
              </option>
            ))}
          </select>
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          <button
            className="btn btn--primary"
            disabled={!writesEnabled || !port}
            onClick={() => ask({ kind: "enable", port })}
          >
            Enable port
          </button>
          <button
            className="btn"
            disabled={!writesEnabled || !port}
            onClick={() => ask({ kind: "disable", port })}
          >
            Disable port
          </button>
          <button
            className="btn"
            disabled={!writesEnabled || !port}
            onClick={() => ask({ kind: "poe", port })}
          >
            Enable PoE
          </button>
          <button
            className="btn"
            disabled={!writesEnabled}
            onClick={() => ask({ kind: "save" })}
          >
            Write memory
          </button>
        </div>

        <div>
          <label className="label">Set description</label>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              className="input"
              maxLength={64}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="e.g. lab-pi-3"
              disabled={!writesEnabled}
            />
            <button
              className="btn"
              disabled={!writesEnabled || !description.trim() || !port}
              onClick={() =>
                ask({ kind: "description", port, description: description.trim() })
              }
            >
              Apply
            </button>
          </div>
        </div>

        {info && (
          <div className="mono" style={{ color: "var(--green)", fontSize: 12 }}>
            {info}
          </div>
        )}
        {error && (
          <div className="mono" style={{ color: "var(--red)", fontSize: 12 }}>
            {error}
          </div>
        )}
      </div>

      {pending && (
        <div className="modal-backdrop" onClick={() => !busy && setPending(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3 className="card__title" style={{ marginBottom: 10 }}>
              Confirm action
            </h3>
            <div style={{ fontSize: 13, lineHeight: 1.55, marginBottom: 14 }}>
              {pending.kind === "enable" &&
                `Enable interface ${pending.port}? This will run "no shutdown".`}
              {pending.kind === "disable" &&
                `Disable interface ${pending.port}? This will run "shutdown".`}
              {pending.kind === "poe" &&
                `Enable PoE on ${pending.port}? This will run "power inline auto".`}
              {pending.kind === "description" &&
                `Set description on ${pending.port} to "${pending.description}".`}
              {pending.kind === "save" &&
                `Run "write memory" to persist running-config to startup-config.`}
            </div>
            <div className="mono" style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 16 }}>
              A config backup will be taken first. Changes will be saved with `write memory`
              and verified.
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
              <button
                className="btn btn--ghost"
                disabled={busy}
                onClick={() => setPending(null)}
              >
                Cancel
              </button>
              <button
                className="btn btn--primary"
                disabled={busy}
                onClick={confirm}
              >
                {busy ? "Applying…" : "Confirm"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
