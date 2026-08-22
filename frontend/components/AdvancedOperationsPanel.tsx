"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { LiveOperationsController } from "@/lib/useLiveOperations";
import type { NetworkInterface, OperationKind } from "@/lib/types";

type Pending =
  | { kind: "unlock" }
  | { kind: "save" }
  | { kind: "operation"; operation: OperationKind; label: string; value?: string };

const WRITABLE_PORTS = new Set([
  "Gi0/3", "Gi0/4", "Gi0/5", "Gi0/6", "Gi0/7", "Gi0/8",
]);

const PROGRESS_STEPS = [
  ["precheck", "Precheck"],
  ["backup", "Backup"],
  ["execute", "Execute"],
  ["verify", "Verify"],
  ["audit", "Audit"],
  ["rollback", "Rollback if needed"],
] as const;

export default function AdvancedOperationsPanel({
  selected,
  live,
}: {
  selected?: NetworkInterface;
  live: LiveOperationsController;
}) {
  const [description, setDescription] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [diagnostic, setDiagnostic] = useState<string | null>(null);

  const port = selected?.port || "";
  const protectedPort = Boolean(selected?.protected);
  const writable = Boolean(port && WRITABLE_PORTS.has(port) && !protectedPort);
  const operation = live.operation?.interface === port ? live.operation : null;
  const controlsReady = live.lock.capability && live.lock.unlocked && writable && !busy;

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
      if (pending.kind === "unlock") {
        await live.unlock();
        setInfo("Device control is unlocked for this app session only.");
      } else if (pending.kind === "save") {
        const result = await live.save();
        setInfo(result.detail);
      } else {
        const result = await live.runOperation(port, pending.operation, pending.value);
        setInfo(
          result.status === "rolled_back"
            ? "Verification failed and the original interface state was restored."
            : result.detail,
        );
        if (pending.operation === "set_description" && result.status === "success") {
          setDescription("");
        }
      }
      setPending(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function runDiagnostics() {
    if (!port) return;
    setBusy(true);
    setError(null);
    setDiagnostic(null);
    try {
      const [state, errors] = await Promise.all([
        api.runGuideOperation("port_state", port),
        api.runGuideOperation("port_errors", port),
      ]);
      setDiagnostic(`${state.explanation} ${errors.explanation}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function lockNow() {
    setBusy(true);
    setError(null);
    try {
      await live.lockNow();
      setInfo("Device control is locked.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  const stageMap = new Map(operation?.stages.map((stage) => [stage.name, stage]));
  return (
    <section className="card advanced-ops" aria-labelledby="advanced-ops-title">
      <div className="card__head">
        <div>
          <div className="eyebrow">Physical IOS control</div>
          <h2 className="card__title" id="advanced-ops-title">
            {port ? `${port} operations` : "Port operations"}
          </h2>
          <div className="card__subtitle">
            Predefined changes only. Every change is backed up, verified and audited on the
            serialized device session.
          </div>
        </div>
        <span className={`badge ${live.lock.unlocked ? "badge--amber" : "badge--green"}`}>
          {live.lock.unlocked ? "CONTROL UNLOCKED" : "CONTROL LOCKED"}
        </span>
      </div>

      {!live.lock.capability ? (
        <div className="ops-lock" role="note">
          <strong>Read-only installation</strong>
          <span>
            Controlled writes are disabled by backend configuration. Diagnostics remain
            read-only and available. Gi0/1, Gi0/2 and Vlan1 stay protected in every mode.
          </span>
        </div>
      ) : live.lock.unlocked ? (
        <div className="ops-lock ops-lock--open" role="note">
          <strong>Unlocked for this session</strong>
          <span>
            Only fixed operations are available. Gi0/1, Gi0/2 and Vlan1 remain protected.
            Restarting SwitchOps locks control again.
          </span>
          <button className="btn btn--ghost" disabled={busy} onClick={lockNow}>
            Lock now
          </button>
        </div>
      ) : (
        <div className="ops-lock" role="note">
          <strong>Controlled writes are locked</strong>
          <span>No physical switch configuration can change until you explicitly unlock.</span>
          <button className="btn" onClick={() => ask({ kind: "unlock" })}>
            Unlock device control for this session
          </button>
        </div>
      )}

      {live.config.runningModified ? (
        <div className="unsaved-config" role="status">
          <div>
            <strong>Running configuration has unsaved changes</strong>
            <span>{live.config.detail}</span>
          </div>
          <button
            className="btn btn--primary"
            disabled={!live.lock.unlocked || busy}
            onClick={() => ask({ kind: "save" })}
          >
            Save configuration
          </button>
        </div>
      ) : (
        <div className="saved-config">
          Running and startup configuration match. SwitchOps never saves automatically.
        </div>
      )}

      <div className="ops-port-summary">
        <div><span>Selected port</span><strong>{port || "None"}</strong></div>
        <div><span>Administrative</span><strong>{selected?.adminState || "unknown"}</strong></div>
        <div><span>Link</span><strong>{selected?.operState || "unknown"}</strong></div>
        <div><span>PoE</span><strong>{selected?.poeState || "unknown"}</strong></div>
      </div>

      {protectedPort ? (
        <div className="ops-boundary">
          This management interface is protected. Physical controls are unavailable; the
          backend also refuses every write attempt to it.
        </div>
      ) : port && !writable ? (
        <div className="ops-boundary">
          {port} is outside the controlled-write allowlist. Read-only diagnostics are still
          available.
        </div>
      ) : null}

      <div className="ops-grid">
        <div className="ops-actions" aria-label={`Bounded actions for ${port || "selected port"}`}>
          <button
            className="btn"
            disabled={!controlsReady}
            onClick={() => ask({ kind: "operation", operation: "admin_up", label: "Enable port" })}
          >
            Enable port
          </button>
          <button
            className="btn"
            disabled={!controlsReady}
            onClick={() => ask({ kind: "operation", operation: "admin_down", label: "Disable port" })}
          >
            Disable port
          </button>
          <button
            className="btn"
            disabled={!controlsReady}
            onClick={() => ask({ kind: "operation", operation: "poe_auto", label: "PoE Auto" })}
          >
            PoE Auto
          </button>
          <button
            className="btn"
            disabled={!controlsReady}
            onClick={() => ask({ kind: "operation", operation: "poe_never", label: "PoE Off" })}
          >
            PoE Off
          </button>
          <button className="btn btn--ghost" disabled={!port || busy} onClick={runDiagnostics}>
            Run diagnostics
          </button>
        </div>

        <label className="ops-field">
          <span className="label">Edit IOS interface description</span>
          <div className="ops-inline">
            <input
              className="input"
              maxLength={64}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder={selected?.description || "e.g. lab-pi-3"}
              disabled={!controlsReady}
            />
            <button
              className="btn"
              disabled={!controlsReady || !description.trim()}
              onClick={() =>
                ask({
                  kind: "operation",
                  operation: "set_description",
                  label: "Edit description",
                  value: description.trim(),
                })
              }
            >
              Review
            </button>
          </div>
          <small>
            This changes physical switch configuration. Topology intent is edited separately
            in “Expected vs observed”.
          </small>
        </label>

        {operation ? (
          <div className="operation-progress" aria-live="polite">
            <div className="operation-progress__head">
              <strong>{operation.status === "running" ? "Operation in progress" : "Operation complete"}</strong>
              <span>{operation.interface}</span>
            </div>
            <ol>
              {PROGRESS_STEPS.map(([name, label]) => {
                const stage = stageMap.get(name);
                const status =
                  stage?.status ||
                  (name === "rollback" && operation.status !== "running" ? "skipped" : "pending");
                return (
                  <li key={name} className={`operation-stage operation-stage--${status}`}>
                    <i aria-hidden />
                    <span><strong>{label}</strong>{stage?.detail ? <small>{stage.detail}</small> : null}</span>
                  </li>
                );
              })}
            </ol>
          </div>
        ) : null}

        {diagnostic ? <div className="ops-diagnostic">{diagnostic}</div> : null}
        {info ? <div className="ops-message ops-message--ok">{info}</div> : null}
        {error ? <div className="ops-message ops-message--error" role="alert">{error}</div> : null}
      </div>

      {pending ? (
        <div className="modal-backdrop" onClick={() => !busy && setPending(null)}>
          <div
            className="modal ops-confirm"
            onClick={(event) => event.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-labelledby="ops-confirm-title"
          >
            <h3 className="card__title" id="ops-confirm-title">
              {pending.kind === "unlock"
                ? "Unlock device control?"
                : pending.kind === "save"
                  ? "Save running configuration?"
                  : `Confirm ${pending.label}`}
            </h3>
            {pending.kind === "unlock" ? (
              <ul className="ops-confirm__list">
                <li>Only predefined operations are available; there is no arbitrary CLI.</li>
                <li>Protected management interfaces remain locked.</li>
                <li>The unlock lasts only for this SwitchOps process.</li>
              </ul>
            ) : pending.kind === "save" ? (
              <p>
                This explicitly writes the current running configuration to startup
                configuration, making all current changes persist after a reboot.
              </p>
            ) : (
              <>
                <p>
                  Apply <strong>{pending.label}</strong> to <strong>{port}</strong>
                  {pending.value ? ` with value “${pending.value}”` : ""}?
                </p>
                <p>
                  SwitchOps will precheck, take a local backup, execute the fixed operation,
                  verify the observed result, and roll back if verification fails.
                </p>
                <div className="ops-confirm__running-only">
                  This changes running-config only. It will not be saved automatically.
                </div>
              </>
            )}
            <div className="ops-confirm__actions">
              <button className="btn btn--ghost" disabled={busy} onClick={() => setPending(null)}>
                Cancel
              </button>
              <button className="btn btn--primary" disabled={busy} onClick={confirm}>
                {busy ? "Working…" : pending.kind === "save" ? "Save configuration" : "Confirm"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
