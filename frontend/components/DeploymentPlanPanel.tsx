"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { DeploymentPlan, InterfaceStatus } from "@/lib/types";

/** Command blocks render one command per line. */
const NEWLINE = "\n";

/** One labelled stage of the plan, so the output reads as a deployment plan. */
function PlanStage({
  label,
  mono,
  children,
}: {
  label: string;
  mono?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="plan-stage">
      <div className="plan-stage__label">
        <span>{label}</span>
        {mono ? <code>{mono}</code> : null}
      </div>
      <div className="plan-stage__body">{children}</div>
    </section>
  );
}

export default function DeploymentPlanPanel({ interfaces }: { interfaces: InterfaceStatus[] }) {
  const safeInterfaces = interfaces.filter((item) => item.policyState === "OPERABLE");
  const [interfaceName, setInterfaceName] = useState(safeInterfaces[0]?.port || "");
  const [vlan, setVlan] = useState(1);
  const [enabled, setEnabled] = useState(true);
  const [poe, setPoe] = useState<"auto" | "never">("auto");
  const [portfast, setPortfast] = useState(true);
  const [planning, setPlanning] = useState(false);
  const [plan, setPlan] = useState<DeploymentPlan | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function generatePlan() {
    setPlanning(true);
    setError(null);
    try {
      setPlan(await api.planAccessPoint({
        interface: interfaceName,
        role: "wireless-access-point",
        enabled,
        vlan,
        poe,
        portfast,
      }));
    } catch (cause) {
      setPlan(null);
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPlanning(false);
    }
  }

  return (
    <section className="card deployment-plan" aria-labelledby="deployment-plan-title">
      <div className="card__head">
        <div>
          <div className="eyebrow">NetDevOps foundation</div>
          <h2 className="card__title" id="deployment-plan-title">Plan an access-point port</h2>
          <div className="card__subtitle">
            Validate a desired state against live read-only evidence and preview bounded IOS. Nothing can be applied.
          </div>
        </div>
        <span className="badge badge--cyan">DRY RUN ONLY</span>
      </div>

      <div className="plan-boundary" role="note">
        <strong>Apply unavailable</strong>
        <span>This planner performs allowlisted show commands only. There is no apply endpoint or execution button.</span>
      </div>

      <div className="plan-form">
        <label>
          <span>Target interface</span>
          <select value={interfaceName} onChange={(event) => setInterfaceName(event.target.value)}>
            {safeInterfaces.map((item) => (
              <option key={item.port} value={item.port}>{item.port} · {item.name || item.status}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Access VLAN</span>
          <input
            type="number"
            min={1}
            max={4094}
            value={vlan}
            onChange={(event) => setVlan(Number(event.target.value))}
          />
        </label>
        <label>
          <span>PoE policy</span>
          <select value={poe} onChange={(event) => setPoe(event.target.value as "auto" | "never")}>
            <option value="auto">Auto</option>
            <option value="never">Never</option>
          </select>
        </label>
        <label className="plan-check-option">
          <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
          <span>Administrative up</span>
        </label>
        <label className="plan-check-option">
          <input type="checkbox" checked={portfast} onChange={(event) => setPortfast(event.target.checked)} />
          <span>Edge PortFast</span>
        </label>
      </div>

      {!safeInterfaces.length ? (
        <div className="plan-boundary" role="note">
          <strong>No operable interfaces</strong>
          <span>Mark a physical interface OPERABLE in Settings before creating a port plan.</span>
        </div>
      ) : null}

      <button type="button" className="btn btn--primary" disabled={planning || !interfaceName} onClick={() => void generatePlan()}>
        {planning ? "Checking current state…" : "Generate dry-run plan"}
      </button>
      {error ? <p className="guide-result guide-result--error" role="alert">{error}</p> : null}

      {plan ? (
        <div className="plan-output" aria-live="polite">
          <PlanStage label="Plan" mono={plan.planId}>
            <h3 className="plan-title">
              Prepare {plan.targetInterface.replace("GigabitEthernet", "Gi")} for an access point
            </h3>
            <span className={`badge ${plan.status === "VALID" ? "badge--green" : "badge--red"}`}>
              {plan.status === "VALID" ? "PLAN IS VALID" : "PLAN IS BLOCKED"}
            </span>
          </PlanStage>

          <PlanStage label="Precheck">
            <ul className="plan-checks">
              {plan.checks.map((check) => (
                <li key={check.name} className={check.passed ? "is-pass" : "is-fail"}>
                  <span>{check.passed ? "PASS" : "BLOCK"}</span>
                  <div>
                    <strong>{check.name.replaceAll("_", " ")}</strong>
                    <small>{check.detail}</small>
                  </div>
                </li>
              ))}
            </ul>
          </PlanStage>

          <PlanStage label="Impact">
            <p className="plan-prose">{plan.impact}</p>
          </PlanStage>

          <PlanStage label="Desired state">
            <dl className="plan-state">
              {Object.entries(plan.desiredState).map(([key, value]) => (
                <div key={key}>
                  <dt>{key.replaceAll("_", " ")}</dt>
                  <dd>{typeof value === "boolean" ? (value ? "enabled" : "disabled") : String(value)}</dd>
                </div>
              ))}
            </dl>
          </PlanStage>

          {plan.proposedIos.length ? (
            <PlanStage label="Proposed IOS">
              <pre className="plan-code">{plan.proposedIos.join(NEWLINE)}</pre>
              <p className="plan-prose plan-prose--dim">
                Preview only. This text is never sent to the device by this build.
              </p>
            </PlanStage>
          ) : null}

          <PlanStage label="Verification">
            <pre className="plan-code plan-code--read">{plan.verificationCommands.join(NEWLINE)}</pre>
            <p className="plan-prose plan-prose--dim">
              Read-only commands that would confirm the change took effect.
            </p>
          </PlanStage>

          <PlanStage label="Backup">
            <p className="plan-prose">
              {plan.backupRequired
                ? "A configuration backup is required before any future apply."
                : "No backup requirement for this plan."}
            </p>
          </PlanStage>

          <div className="plan-no-apply">
            <strong>DRY RUN ONLY</strong>
            <span>No configuration will be sent to the device. There is no apply endpoint.</span>
          </div>
        </div>
      ) : null}
    </section>
  );
}
