import type { DiscoveryStatus } from "@/lib/types";

function stateTone(state: string): string {
  if (state === "confirmed" || state === "enabled") return "discovery-card--good";
  if (state === "ambiguous") return "discovery-card--warn";
  return "discovery-card--neutral";
}

export default function DiscoveryStatusPanel({ discovery }: { discovery: DiscoveryStatus }) {
  const { lldp, localEndpoint, snmp } = discovery;
  return (
    <section className="panel discovery-panel" aria-labelledby="discovery-title">
      <header className="panel__header discovery-panel__header">
        <div>
          <p className="eyebrow">PROGRESSIVE IDENTITY</p>
          <h2 id="discovery-title">What the evidence can identify</h2>
        </div>
        <span className="badge badge--outline">READ ONLY</span>
      </header>
      <div className="discovery-grid">
        <article className={`discovery-card ${stateTone(lldp.state)}`}>
          <span className="discovery-card__label">LLDP</span>
          <strong>{lldp.state.toUpperCase()}</strong>
          <p>{lldp.detail}</p>
          {lldp.neighbors.length ? (
            <small>{lldp.neighbors.map((neighbor) => `${neighbor.remoteName} on ${neighbor.localInterface}`).join(" · ")}</small>
          ) : null}
        </article>
        <article className={`discovery-card ${stateTone(localEndpoint.state)}`}>
          <span className="discovery-card__label">THIS PC</span>
          <strong>
            {localEndpoint.state === "confirmed"
              ? `${localEndpoint.label} · ${localEndpoint.interface}`
              : localEndpoint.state.replace("-", " ").toUpperCase()}
          </strong>
          <p>{localEndpoint.detail}</p>
        </article>
        <article className={`discovery-card ${snmp.readWriteCommunities ? "discovery-card--warn" : "discovery-card--neutral"}`}>
          <span className="discovery-card__label">SNMP</span>
          <strong>{snmp.configured ? `EXISTING · ${snmp.versions.join(" + ") || "VERSION UNKNOWN"}` : "NOT CONFIGURED"}</strong>
          <p>{snmp.detail}</p>
          {snmp.configured ? (
            <small>
              RO communities {snmp.readOnlyCommunities} · RW communities {snmp.readWriteCommunities} · v3 users {snmp.v3Users}
            </small>
          ) : null}
        </article>
      </div>
    </section>
  );
}
