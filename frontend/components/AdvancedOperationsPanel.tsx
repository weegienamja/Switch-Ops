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

/**
 * Direct allowlisted device operations.
 *
 * These used to sit at the bottom of the Lab Guide, which put raw
 * administrative buttons on a page whose whole promise is read-only learning.
 * They belong in Change control instead: the same guarded actions, presented
 * as the end of a plan-review-apply flow rather than as a beginner control.
 *
 * The backend still refuses every one of these unless ENABLE_WRITE_ACTIONS is
 * set, and refuses the protected interfaces regardless.
 */
export default function AdvancedOperationsPanel({
  setup,
  interfaces,
  onChange,
}: {
  setup: SetupStatus;
  interfaces: InterfaceStatus[];
  onChange: () => void;
}) {
  const writable = interfaces.filter((item) => WRITABLE_PORTS.has(item.port));
  const [port, setPort] = useState(writable[0]?.port || "");
  const [description, setDescription] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const writesEnabled = setup.enableWriteActions;

  function ask(next: Pending) {
    setError(null);
    setInfo(null);
    setPending(next);
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
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card advanced-ops" aria-labelledby="advanced-ops-title">
      <div className="card__head">
        <div>
          <div className="eyebrow">Change control</div>
          <h2 className="card__title" id="advanced-ops-title">Advanced operations</h2>
          <div className="card__subtitle">
            Direct allowlisted actions on Gi0/3–Gi0/8. Each one backs up the configuration, makes
            the change, saves it, then verifies.
          </div>
        </div>
        <span className={`badge ${writesEnabled ? "badge--amber" : "badge--green"}`}>
          {writesEnabled ? "WRITES ENABLED" : "WRITES DISABLED"}
        </span>
      </div>

      <div className={`ops-lock ${writesEnabled ? "ops-lock--open" : ""}`} role="note">
        <strong>{writesEnabled ? "Write mode is on" : "Read-only"}</strong>
        <span>
          {writesEnabled
            ? "This backend is configured to allow safe writes. Gi0/1, Gi0/2 and Vlan1 stay protected and are refused regardless."
            : "SwitchOps cannot change this switch. Enable writes deliberately by setting ENABLE_WRITE_ACTIONS=true in the backend environment; Gi0/1, Gi0/2 and Vlan1 stay protected even then."}
        </span>
      </div>

      <div className="ops-grid">
        <label className="ops-field">
          <span className="label">Interface</span>
          <select
            className="input"
            value={port}
            onChange={(event) => setPort(event.target.value)}
            disabled={!writesEnabled}
          >
            {writable.map((item) => (
              <option key={item.port} value={item.port}>
                {item.port} — {item.name || "no description"}
              </option>
            ))}
          </select>
        </label>

        <div className="ops-actions">
          <button
            className="btn"
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

        <label className="ops-field">
          <span className="label">Set description</span>
          <div className="ops-inline">
            <input
              className="input"
              maxLength={64}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="e.g. lab-pi-3"
              disabled={!writesEnabled}
            />
            <button
              className="btn"
              disabled={!writesEnabled || !description.trim() || !port}
              onClick={() => ask({ kind: "description", port, description: description.trim() })}
            >
              Apply
            </button>
          </div>
        </label>

        {info ? <div className="ops-message ops-message--ok">{info}</div> : null}
        {error ? <div className="ops-message ops-message--error" role="alert">{error}</div> : null}
      </div>

      {pending ? (
        <div className="modal-backdrop" onClick={() => !busy && setPending(null)}>
          <div className="modal" onClick={(event) => event.stopPropagation()} role="dialog" aria-modal="true">
            <h3 className="card__title" style={{ marginBottom: 10 }}>Confirm action</h3>
            <div style={{ fontSize: 13, lineHeight: 1.55, marginBottom: 14 }}>
              {pending.kind === "enable" && `Enable interface ${pending.port}? This will run "no shutdown".`}
              {pending.kind === "disable" && `Disable interface ${pending.port}? This will run "shutdown".`}
              {pending.kind === "poe" && `Enable PoE on ${pending.port}? This will run "power inline auto".`}
              {pending.kind === "description" && `Set description on ${pending.port} to "${pending.description}".`}
              {pending.kind === "save" && `Run "write memory" to persist running-config to startup-config.`}
            </div>
            <div className="mono" style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 16 }}>
              A config backup will be taken first. Changes will be saved with `write memory` and verified.
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
              <button className="btn btn--ghost" disabled={busy} onClick={() => setPending(null)}>
                Cancel
              </button>
              <button className="btn btn--primary" disabled={busy} onClick={confirm}>
                {busy ? "Applying…" : "Confirm"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
