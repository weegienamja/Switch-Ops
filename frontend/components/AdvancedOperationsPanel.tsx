"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { LiveOperationsController } from "@/lib/useLiveOperations";
import type { ChangeSession, NetworkInterface, OperationKind } from "@/lib/types";

type Pending =
  | { kind: "unlock" }
  | { kind: "save" }
  | { kind: "execute"; session: ChangeSession };

const PROGRESS_STEPS = [
  ["precheck", "Primitive precheck"],
  ["backup", "Configuration backup"],
  ["execute", "Bounded execution"],
  ["verify", "Target verification"],
  ["audit", "Audit record"],
  ["rollback", "Rollback if needed"],
] as const;

const TERMINAL = new Set([
  "blocked",
  "rolled_back",
  "succeeded",
  "succeeded_with_warnings",
  "indeterminate",
]);

function displayStatus(status: string): string {
  return status.replaceAll("_", " ").toUpperCase();
}

export default function AdvancedOperationsPanel({
  selected,
  live,
}: {
  selected?: NetworkInterface;
  live: LiveOperationsController;
}) {
  const [description, setDescription] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [reviewed, setReviewed] = useState<ChangeSession | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [diagnostic, setDiagnostic] = useState<string | null>(null);

  const port = selected?.port || "";
  const policyState = selected?.policyState || "UNMANAGED";
  const protectedPort = policyState === "PROTECTED";
  const writable = Boolean(port && policyState === "OPERABLE");
  // Planning and preflight are read-only even when policy will block execution.
  // Keeping them available lets the operator see the durable blocking evidence.
  const canPlan = Boolean(port) && !busy;
  const streamed =
    live.changeSession && live.changeSession.plan.targetInterface === port
      ? live.changeSession
      : null;
  const session = reviewed
    ? streamed?.id === reviewed.id
      ? streamed
      : reviewed
    : streamed;
  const operation = live.operation?.interface === port ? live.operation : null;
  const operationStages = session?.operationStages?.length
    ? session.operationStages
    : operation?.stages || [];
  const stageMap = new Map(operationStages.map((stage) => [stage.name, stage]));

  function ask(next: Pending) {
    setError(null);
    setInfo(null);
    setPending(next);
  }

  async function reviewChange(kind: OperationKind, value?: string) {
    if (!port) return;
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const created = await api.createChangeSession(port, kind, value);
      const preflight = await api.preflightChangeSession(created.id);
      setReviewed(preflight);
      setInfo(
        preflight.status === "ready"
          ? "Read-only preflight passed. Review the evidence before choosing Execute."
          : "Preflight blocked execution. Review the blocking evidence below.",
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function rerunPreflight() {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      const next = await api.preflightChangeSession(session.id);
      setReviewed(next);
      setInfo(next.status === "ready" ? "Preflight passed with current evidence." : "Preflight remains blocked.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
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
        const result = await live.runChangeSession(pending.session.id);
        setReviewed(result);
        setInfo(result.outcomeDetail);
        if (
          result.plan.steps[0]?.kind === "set_description" &&
          result.status === "succeeded"
        ) {
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

  const executing = session && ["executing", "verifying", "rolling_back"].includes(session.status);
  const finished = session && TERMINAL.has(session.status);
  return (
    <section className="card advanced-ops" aria-labelledby="advanced-ops-title">
      <div className="card__head">
        <div>
          <div className="eyebrow">Change Assurance · bounded IOS control</div>
          <h2 className="card__title" id="advanced-ops-title">
            {port ? `${port} change plan` : "Port change plan"}
          </h2>
          <div className="card__subtitle">
            Plan and preflight are read-only. Execute still requires every independent write
            gate, a fresh before-state, backup, verification and audit.
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
            Change plans and preflight evidence remain available, but execution is blocked
            while controlled writes are disabled. Protected and unmanaged interfaces cannot
            be changed; policy and discovery remain separate.
          </span>
        </div>
      ) : live.lock.unlocked ? (
        <div className="ops-lock ops-lock--open" role="note">
          <strong>Unlocked for this session</strong>
          <span>
            Only reviewed, single-step bounded plans can execute. Protected and unmanaged
            interfaces remain read-only.
          </span>
          <button className="btn btn--ghost" disabled={busy} onClick={lockNow}>
            Lock now
          </button>
        </div>
      ) : (
        <div className="ops-lock" role="note">
          <strong>Controlled writes are locked</strong>
          <span>Planning and preflight do not require unlock. Execution does.</span>
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
          Running and startup configuration match. Change sessions never save automatically.
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
          This interface is protected by the local device policy. Planning may describe it, but the
          backend will never authorize execution.
        </div>
      ) : port && !writable ? (
        <div className="ops-boundary">
          {port} is unmanaged. Marking it OPERABLE in Settings is a separate deliberate
          safety decision; network evidence cannot do that automatically.
        </div>
      ) : null}

      <div className="ops-grid">
        <div className="ops-actions" aria-label={`Bounded plans for ${port || "selected port"}`}>
          <button className="btn" disabled={!canPlan} onClick={() => void reviewChange("admin_up")}>
            Review enable
          </button>
          <button className="btn" disabled={!canPlan} onClick={() => void reviewChange("admin_down")}>
            Review disable
          </button>
          <button className="btn" disabled={!canPlan} onClick={() => void reviewChange("poe_auto")}>
            Review PoE auto
          </button>
          <button className="btn" disabled={!canPlan} onClick={() => void reviewChange("poe_never")}>
            Review PoE off
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
              disabled={!canPlan}
            />
            <button
              className="btn"
              disabled={!canPlan || !description.trim()}
              onClick={() => void reviewChange("set_description", description.trim())}
            >
              Review
            </button>
          </div>
          <small>
            The description is switch configuration and expected intent, not observed device identity.
          </small>
        </label>

        {session ? (
          <div className={`change-review change-review--${session.status}`} aria-live="polite">
            <div className="change-review__head">
              <div>
                <span className="eyebrow">Durable change session</span>
                <strong>{session.plan.declaredIntent.summary}</strong>
              </div>
              <span className="change-review__status">{displayStatus(session.status)}</span>
            </div>

            {session.preflight ? (
              <>
                <div className="change-impact">
                  <div><span>Control path</span><strong>{session.preflight.impact.controlPath}</strong></div>
                  <div><span>Attached</span><strong>{session.preflight.impact.attachedEndpoints}</strong></div>
                  <div><span>Learned behind</span><strong>{session.preflight.impact.learnedBehind}</strong></div>
                  <div><span>Expected</span><strong>{session.preflight.impact.expectedRelationship || "none"}</strong></div>
                </div>
                <p className="change-review__control">{session.preflight.impact.controlPathDetail}</p>
                <ul className="change-checks">
                  {session.preflight.checks.map((check) => (
                    <li key={check.code} className={`change-check change-check--${check.status}`}>
                      <span>{check.status.toUpperCase()}</span>
                      <div><strong>{check.label}</strong><small>{check.detail}</small></div>
                    </li>
                  ))}
                </ul>
                <details className="change-effects">
                  <summary>Declared effects and limits</summary>
                  <ul>
                    {session.plan.declaredIntent.expectedPostconditions.map((effect) => (
                      <li key={`${effect.category}-${effect.field}`}>
                        <strong>{effect.field}</strong> · {effect.expectation}
                      </li>
                    ))}
                  </ul>
                </details>
              </>
            ) : null}

            {session.status === "ready" ? (
              <div className="change-review__actions">
                <button className="btn btn--ghost" disabled={busy} onClick={() => void rerunPreflight()}>
                  Refresh preflight
                </button>
                <button
                  className="btn btn--primary"
                  disabled={busy || !live.lock.unlocked}
                  onClick={() => ask({ kind: "execute", session })}
                >
                  Execute reviewed change
                </button>
                {!live.lock.unlocked ? <small>Unlock control before execution.</small> : null}
              </div>
            ) : session.status === "blocked" ? (
              <div className="change-review__actions">
                <button className="btn btn--ghost" disabled={busy} onClick={() => void rerunPreflight()}>
                  Re-run preflight
                </button>
                <small>No IOS configuration was attempted.</small>
              </div>
            ) : null}

            {executing || session.operationStages.length ? (
              <div className="operation-progress">
                <div className="operation-progress__head">
                  <strong>{executing ? "Change in progress" : "Bounded transaction"}</strong>
                  <span>{session.plan.targetInterface}</span>
                </div>
                <ol>
                  {PROGRESS_STEPS.map(([name, label]) => {
                    const stage = stageMap.get(name);
                    const status = stage?.status || (name === "rollback" && finished ? "skipped" : "pending");
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

            {finished ? (
              <div className="change-result">
                <strong>{session.outcomeDetail}</strong>
                {session.beforeSnapshot && session.afterSnapshot ? (
                  <div className="change-before-after">
                    <div>
                      <span>Before</span>
                      <strong>{session.beforeSnapshot.target.adminState} · {session.beforeSnapshot.target.operState}</strong>
                      <small>PoE {session.beforeSnapshot.target.poeAdmin} · errors {session.beforeSnapshot.target.errorTotal}</small>
                    </div>
                    <div>
                      <span>After</span>
                      <strong>{session.afterSnapshot.target.adminState} · {session.afterSnapshot.target.operState}</strong>
                      <small>PoE {session.afterSnapshot.target.poeAdmin} · errors {session.afterSnapshot.target.errorTotal}</small>
                    </div>
                  </div>
                ) : null}
                {session.comparison ? (
                  <>
                    <p>{session.comparison.summary}</p>
                    {session.comparison.differences.length ? (
                      <ul className="change-differences">
                        {session.comparison.differences.map((difference, index) => (
                          <li key={`${difference.scope}-${difference.field}-${index}`} className={`change-difference--${difference.assessment}`}>
                            <span>{difference.assessment.toUpperCase()}</span>{difference.detail}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </>
                ) : null}
                {session.operationResult?.requiresSave ? (
                  <div className="ops-confirm__running-only">
                    Verified in running configuration. Startup configuration remains unchanged until an explicit save.
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}

        {diagnostic ? <div className="ops-diagnostic">{diagnostic}</div> : null}
        {info ? <div className="ops-message ops-message--ok">{info}</div> : null}
        {error ? <div className="ops-message ops-message--error" role="alert">{error}</div> : null}
      </div>

      {pending ? (
        <div className="modal-backdrop" onClick={() => !busy && setPending(null)}>
          <div className="modal ops-confirm" onClick={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-labelledby="ops-confirm-title">
            <h3 className="card__title" id="ops-confirm-title">
              {pending.kind === "unlock"
                ? "Unlock device control?"
                : pending.kind === "save"
                  ? "Save running configuration?"
                  : "Execute this reviewed change?"}
            </h3>
            {pending.kind === "unlock" ? (
              <ul className="ops-confirm__list">
                <li>Only predefined operations are available; there is no arbitrary CLI.</li>
                <li>Protected and unmanaged interfaces remain read-only.</li>
                <li>The unlock lasts only for this SwitchOps process.</li>
              </ul>
            ) : pending.kind === "save" ? (
              <p>
                This explicitly writes the current running configuration to startup
                configuration, making all current changes persist after a reboot.
              </p>
            ) : (
              <>
                <p><strong>{pending.session.plan.declaredIntent.summary}</strong></p>
                <p>
                  SwitchOps will capture a fresh before-state under stable authorization,
                  recheck every blocker, run the existing bounded transaction, and compare
                  target, configuration, topology and health observations afterward.
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
                {busy ? "Working…" : pending.kind === "save" ? "Save configuration" : pending.kind === "execute" ? "Execute" : "Confirm"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
