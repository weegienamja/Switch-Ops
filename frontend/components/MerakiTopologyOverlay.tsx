import type { UnifiedLabState } from "@/lib/unifiedTypes";

export default function MerakiTopologyOverlay({ state }: { state: UnifiedLabState }) {
  const merakiEntities = state.entities.filter((entity) =>
    entity.providers.includes("meraki-dashboard"),
  );
  const merakiOnly = merakiEntities.filter((entity) => entity.providers.length === 1);
  const merakiClaimIds = new Set(
    state.claims
      .filter((claim) => claim.provider === "meraki-dashboard")
      .map((claim) => claim.id),
  );
  const relationships = state.relationships.filter((relationship) =>
    relationship.providerClaimIds.some((id) => merakiClaimIds.has(id)),
  );
  const source = state.sourceHealth.find((item) => item.provider === "meraki-dashboard");

  return (
    <section className="card meraki-overlay">
      <div className="card__head">
        <div>
          <div className="eyebrow">Meraki overlay</div>
          <h2>Evidence the Catalyst-only drawing cannot prove</h2>
        </div>
        <span className={`cross-state cross-state--${source?.state === "healthy" ? "good" : source?.state === "unavailable" ? "bad" : "warn"}`}>
          {source?.state || "not configured"}
        </span>
      </div>
      {merakiEntities.length ? (
        <div className="meraki-overlay__grid">
          <div>
            <span>Meraki-backed entities</span>
            <strong>{merakiEntities.length}</strong>
            <small>{merakiOnly.length} currently visible from Meraki only</small>
          </div>
          <div>
            <span>Normalized relationships</span>
            <strong>{relationships.length}</strong>
            <small>Dashed conceptual overlay; Catalyst geometry is unchanged</small>
          </div>
          <ul>
            {merakiOnly.slice(0, 6).map((entity) => (
              <li key={entity.id}>
                <span className="provider-badge provider-badge--meraki-dashboard">Meraki</span>
                <strong>{entity.label}</strong>
                <small>{entity.category} · {entity.freshness}</small>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="empty-note">Configure the optional Meraki source in Settings to add a read-only overlay.</p>
      )}
    </section>
  );
}
