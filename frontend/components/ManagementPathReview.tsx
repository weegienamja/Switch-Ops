import type {
  ManagementPathAssurance,
  MerakiManagementEvidence,
  RecoveryPlan,
} from "@/lib/api";


function timestamp(value?: string | null): string {
  if (!value) return "No current observation";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}


function statusTone(status: RecoveryPlan["status"]): string {
  if (status === "READY" || status === "NOT_NEEDED") return "good";
  if (status === "BLOCKED") return "bad";
  return "warn";
}


function ReviewList({
  title,
  items,
}: {
  title: string;
  items: string[];
}) {
  if (!items.length) return null;
  return (
    <div className="recovery-plan__section">
      <strong>{title}</strong>
      <ul>{items.map((item, index) => <li key={`${title}-${index}`}>{item}</li>)}</ul>
    </div>
  );
}


export function MerakiEvidenceSummary({
  evidence,
}: {
  evidence: MerakiManagementEvidence;
}) {
  const catalystPorts = evidence.ports.filter((port) => port.catalystFacing);
  return (
    <section className="meraki-path-evidence" aria-label="Meraki management-path evidence">
      <div className="management-review__head">
        <strong>Observed current Meraki configuration</strong>
        <span className={`evidence-freshness evidence-freshness--${evidence.freshness}`}>
          {evidence.state} · {evidence.freshness}
        </span>
      </div>
      <span>{evidence.detail}</span>
      <span>Observed: {timestamp(evidence.observedAt)}</span>
      {evidence.lans.length ? (
        <ul>
          {evidence.lans.map((lan) => (
            <li key={`${lan.vlanId || "single"}-${lan.subnet}`}>
              <b>{lan.subnet}</b>
              {lan.vlanId ? ` · VLAN ${lan.vlanId}` : " · single LAN"}
              {lan.applianceIp ? ` · appliance ${lan.applianceIp}` : ""}
              {` · DHCP ${lan.dhcpMode}`}
              {lan.dhcpMode === "relay" ? ` (${lan.dhcpRelayServerCount} relays)` : ""}
            </li>
          ))}
        </ul>
      ) : (
        <span>No normalized MX LAN configuration is available.</span>
      )}
      {catalystPorts.length ? (
        <ul>
          {catalystPorts.map((port) => (
            <li key={port.portId}>
              <b>MX port {port.portId}</b> · Catalyst-facing · {port.mode}
              {port.accessVlan ? ` · access VLAN ${port.accessVlan}` : ""}
              {port.nativeVlan ? ` · native VLAN ${port.nativeVlan}` : ""}
              {port.allowedVlans.length ? ` · allowed ${port.allowedVlans.join(", ")}` : ""}
            </li>
          ))}
        </ul>
      ) : (
        <span>The Catalyst-facing MX port is not identified by current evidence.</span>
      )}
    </section>
  );
}


export function RecoveryPlanReview({
  plan,
  compact = false,
}: {
  plan: RecoveryPlan;
  compact?: boolean;
}) {
  const operation = plan.operation;
  const architecture = plan.executionArchitecture;
  const proposed = operation.kind === "TEMPORARY_SECONDARY_IPV4"
    ? [
        `Adapter: ${operation.adapterId || "not established"}`,
        `Temporary address: ${operation.candidateAddress || "candidate not established"}${
          operation.prefixLength == null ? "" : `/${operation.prefixLength}`
        }`,
        `Gateway: none · expected on-link route: ${operation.expectedRoute || "not established"}`,
      ]
    : [];
  return (
    <section
      className={`recovery-plan ${compact ? "recovery-plan--compact" : ""}`}
      aria-label="Recovery assessment"
    >
      <div className="management-review__head">
        <div>
          <strong>Recovery assessment</strong>
          <span>{plan.headline}</span>
        </div>
        <span className={`recovery-status recovery-status--${statusTone(plan.status)}`}>
          {plan.status}
        </span>
      </div>
      <p>{plan.summary}</p>
      <div className="planning-only">
        Planning only · no executor or approval control exists
      </div>
      <details>
        <summary>Review recovery plan</summary>
        <div className="recovery-plan__body">
          <ReviewList title="Proposed bounded change" items={proposed} />
          <ReviewList
            title="Blockers"
            items={plan.blockers.map((blocker) => `${blocker.code}: ${blocker.summary}`)}
          />
          <ReviewList title="Expected effect" items={plan.expectedEffect} />
          <ReviewList title="What would not change" items={plan.unchangedState} />
          <ReviewList title="Verification" items={plan.verificationSteps} />
          <ReviewList title="Rollback" items={plan.rollbackSteps} />
          <ReviewList title="Missing evidence" items={plan.missingEvidence} />
          <ReviewList title="Safety warnings" items={plan.warnings} />
          <ReviewList
            title="Execution gate"
            items={architecture.gate.reasons.map((reason) =>
              reason.replaceAll("_", " ")
            )}
          />
          <ReviewList
            title="Authority"
            items={[
              `Current policy: ${architecture.authority.currentPolicy}`,
              `Future host recovery boundary: ${architecture.authority.requiredLevel}`,
              "Future execution would require administrator elevation and explicit operator approval.",
              "Automatic host-network recovery is disabled.",
            ]}
          />
          <ReviewList
            title="Candidate primitive — not implemented"
            items={architecture.primitive.rationale}
          />
          <ReviewList
            title="Transaction and preservation"
            items={architecture.transaction.preservationInvariants}
          />
          <ReviewList
            title="Rollback triggers"
            items={architecture.transaction.rollbackTriggers}
          />
          <ReviewList
            title="Ownership identity"
            items={architecture.ownership.identityFields}
          />
          <div className="recovery-plan__binding mono">
            <strong>Crash recovery</strong>
            <span>{architecture.transaction.restartBehavior}</span>
          </div>
          <div className="recovery-plan__binding mono">
            <strong>State binding</strong>
            <span>Evidence: {timestamp(plan.binding.evidenceObservedAt)}</span>
            <span>Plan fingerprint: {plan.binding.stateFingerprint.slice(0, 16)}…</span>
            <span>
              Any relevant host, route, lease, adapter, target, or diagnosis change
              requires replanning.
            </span>
          </div>
        </div>
      </details>
    </section>
  );
}


export default function ManagementPathReview({
  assurance,
}: {
  assurance: ManagementPathAssurance;
}) {
  return (
    <div className="management-path-review">
      <MerakiEvidenceSummary evidence={assurance.merakiEvidence} />
      <RecoveryPlanReview plan={assurance.recoveryPlan} />
    </div>
  );
}
