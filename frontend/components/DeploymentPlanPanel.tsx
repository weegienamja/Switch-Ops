"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { DeploymentPlan, InterfaceStatus } from "@/lib/types";

export default function DeploymentPlanPanel({ interfaces }: { interfaces: InterfaceStatus[] }) {
  const safeInterfaces = interfaces.filter((item) => !item.protected);
  const [interfaceName, setInterfaceName] = useState(
    safeInterfaces.find((item) => item.port === "Gi0/4")?.port || safeInterfaces[0]?.port || "Gi0/4",
  );
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

      <button type="button" className="btn btn--primary" disabled={planning} onClick={() => void generatePlan()}>
        {planning ? "Checking current state…" : "Generate dry-run plan"}
      </button>
      {error ? <p className="guide-result guide-result--error" role="alert">{error}</p> : null}

      {plan ? (
        <div className="plan-output" aria-live="polite">
          <div className="plan-output__head">
            <div>
              <div className="eyebrow">{plan.planId}</div>
              <h3>{plan.status === "VALID" ? "Plan is valid" : "Plan is blocked"}</h3>
            </div>
            <span className={`badge ${plan.status === "VALID" ? "badge--green" : "badge--red"}`}>
              {plan.status}
            </span>
          </div>
          <p>{plan.impact}</p>
          <ul className="plan-checks">
            {plan.checks.map((check) => (
              <li key={check.name} className={check.passed ? "is-pass" : "is-fail"}>
                <span aria-hidden>{check.passed ? "PASS" : "BLOCK"}</span>
                <div><strong>{check.name.replaceAll("_", " ")}</strong><small>{check.detail}</small></div>
              </li>
            ))}
          </ul>
          {plan.proposedIos.length ? (
            <details className="plan-commands" open>
              <summary>Proposed IOS — preview only</summary>
              <pre>{plan.proposedIos.join("\n")}</pre>
            </details>
          ) : null}
          <details className="plan-commands">
            <summary>Read-only verification commands</summary>
            <pre>{plan.verificationCommands.join("\n")}</pre>
          </details>
          <div className="plan-no-apply">APPLY UNAVAILABLE · {plan.backupRequired ? "backup required before any future change" : "no backup requirement"}</div>
        </div>
      ) : null}
    </section>
  );
}
