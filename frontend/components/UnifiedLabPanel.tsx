"use client";

import type {
  CrossProviderState,
  IdentityLink,
  ProviderKind,
  UnifiedLabState,
} from "@/lib/unifiedTypes";

function providerLabel(provider: ProviderKind): string {
  if (provider === "catalyst-ios") return "Catalyst";
  if (provider === "meraki-dashboard") return "Meraki";
  if (provider === "switchops-intent") return "Intent";
  return "History";
}

function stateTone(state: CrossProviderState | string): string {
  if (state === "AGREED" || state === "healthy" || state === "confirmed") return "good";
  if (state === "CONFLICT" || state === "conflicted" || state === "unavailable") return "bad";
  if (state === "AMBIGUOUS" || state === "partial" || state === "rate-limited") return "warn";
  return "neutral";
}

export default function UnifiedLabPanel({
  state,
  busy,
  onRefreshMeraki,
  onDecision,
}: {
  state: UnifiedLabState;
  busy?: string | null;
  onRefreshMeraki: () => void;
  onDecision: (linkId: string, decision: "confirm" | "reject" | "clear") => void;
}) {
  const providerEntities = new Map(state.providerEntities.map((item) => [item.id, item]));
  const claims = new Map(state.claims.map((item) => [item.id, item]));
  const candidates = state.identityLinks.filter((item) =>
    ["candidate", "rejected", "conflicted"].includes(item.state)
      || (item.state === "confirmed" && !item.automatic),
  );
  const providerOnly = state.entities.filter((item) => item.identityState === "PROVIDER_ONLY").length;
  const disagreements = state.entities.filter((item) =>
    item.attributes.some((attribute) => attribute.state === "CONFLICT"),
  ).length;

  return (
    <div className="unified-lab">
      <section className="card unified-source-card">
        <div className="card__head unified-heading">
          <div>
            <div className="eyebrow">Unified Lab</div>
            <h2>Evidence from both worlds, without silent merging</h2>
            <p>
              Catalyst and Meraki remain independent sources. Strong identifiers can join records;
              ambiguous evidence stays visible for a local decision.
            </p>
          </div>
          <button className="btn" onClick={onRefreshMeraki} disabled={busy === "refresh"}>
            {busy === "refresh" ? "Refreshing Meraki…" : "Refresh Meraki evidence"}
          </button>
        </div>
        <div className="source-health-grid">
          {state.sourceHealth.map((source) => (
            <article className={`source-health source-health--${stateTone(source.state)}`} key={source.provider}>
              <div>
                <span className={`provider-badge provider-badge--${source.provider}`}>
                  {providerLabel(source.provider)}
                </span>
                <strong>{source.state.replaceAll("-", " ")}</strong>
              </div>
              <p>{source.detail}</p>
              <small>
                Checked {new Date(source.checkedAt).toLocaleString()}
                {source.failedOperations.length ? ` · missing ${source.failedOperations.join(", ")}` : ""}
              </small>
            </article>
          ))}
        </div>
      </section>

      <section className="unified-counts" aria-label="Unified evidence summary">
        <div><span>Unified entities</span><strong>{state.entities.length}</strong></div>
        <div><span>Provider only</span><strong>{providerOnly}</strong></div>
        <div><span>Identity decisions</span><strong>{candidates.length}</strong></div>
        <div><span>Attribute conflicts</span><strong>{disagreements}</strong></div>
      </section>

      {candidates.length ? (
        <section className="card unified-decisions">
          <div className="card__head">
            <div>
              <div className="eyebrow">Identity candidates</div>
              <h2>Relationships requiring evidence or an operator decision</h2>
            </div>
          </div>
          <div className="unified-decision-list">
            {candidates.map((link) => (
              <IdentityCandidate
                key={link.id}
                link={link}
                left={providerEntities.get(link.leftEntityId)?.label || "Unknown entity"}
                right={providerEntities.get(link.rightEntityId)?.label || "Unknown entity"}
                busy={busy === link.id}
                onDecision={onDecision}
              />
            ))}
          </div>
        </section>
      ) : null}

      <section className="card unified-inventory">
        <div className="card__head">
          <div>
            <div className="eyebrow">Unified Inventory</div>
            <h2>Entities and independently reconciled attributes</h2>
          </div>
          <span className="badge">{state.entities.length} entities</span>
        </div>
        {state.entities.length ? (
          <div className="unified-entity-list">
            {state.entities.map((entity) => {
              const evidence = entity.evidenceIds
                .map((id) => claims.get(id))
                .filter((item) => item !== undefined);
              const records = entity.providerEntityIds
                .map((id) => providerEntities.get(id))
                .filter((item) => item !== undefined);
              return (
                <article className="unified-entity" key={entity.id}>
                  <div className="unified-entity__summary">
                    <div className="unified-entity__identity">
                      <span className="unified-entity__category">{entity.category}</span>
                      <strong>{entity.label}</strong>
                      <div className="provider-badges">
                        {entity.providers.map((provider) => (
                          <span className={`provider-badge provider-badge--${provider}`} key={provider}>
                            {providerLabel(provider)}
                          </span>
                        ))}
                      </div>
                    </div>
                    <span className={`cross-state cross-state--${stateTone(entity.identityState)}`}>
                      identity {entity.identityState}
                    </span>
                  </div>
                  <div className="attribute-grid">
                    {entity.attributes.map((attribute) => (
                      <div key={attribute.field} title={attribute.explanation}>
                        <span>{attribute.field}</span>
                        <strong className={`cross-state cross-state--${stateTone(attribute.state)}`}>
                          {attribute.state}
                        </strong>
                        {attribute.value !== null && attribute.value !== undefined ? (
                          <small>{String(attribute.value)}</small>
                        ) : null}
                      </div>
                    ))}
                  </div>
                  <details className="evidence-inspector">
                    <summary>
                      Evidence inspector · {evidence.length} claims · {records.length} provider records
                    </summary>
                    <div className="evidence-inspector__body">
                      {records.map((record, index) => (
                        <div className="provider-record" key={`${record.id}-${index}`}>
                          <span className={`provider-badge provider-badge--${record.provider}`}>
                            {providerLabel(record.provider)}
                          </span>
                          <strong>{record.label}</strong>
                          <small>
                            {record.identifiers.map((identifier) => `${identifier.kind} (${identifier.strength})`).join(" · ") || "No durable identifier"}
                          </small>
                        </div>
                      ))}
                      <ul className="claim-list">
                        {evidence.map((item, index) => item ? (
                          <li key={`${item.id}-${index}`}>
                            <div>
                              <strong>{item.field}</strong>
                              <span>{item.strength} · {item.freshness}</span>
                            </div>
                            <p>{item.detail || `${item.provider} reports ${String(item.value ?? "a relationship")}.`}</p>
                            <small>
                              {providerLabel(item.provider)} · {item.provenance.sourceKind} · observed {new Date(item.provenance.observedAt).toLocaleString()}
                              {!item.provenance.complete ? " · partial collection" : ""}
                            </small>
                          </li>
                        ) : null)}
                      </ul>
                    </div>
                  </details>
                </article>
              );
            })}
          </div>
        ) : (
          <p className="empty-note unified-empty">
            No normalized entity evidence yet. Catalyst appears after a deep observation; Meraki is optional.
          </p>
        )}
      </section>

      <div className="grid grid--12">
        <section className="card col-6 unified-relationships">
          <div className="card__head"><h2>Cross-provider relationships</h2></div>
          {state.relationships.length ? (
            <ul>
              {state.relationships.map((relationship) => (
                <li key={relationship.id}>
                  <span className={`cross-state cross-state--${stateTone(relationship.state)}`}>
                    {relationship.state}
                  </span>
                  <strong>{relationship.relationship}</strong>
                  <p>{relationship.explanation}</p>
                </li>
              ))}
            </ul>
          ) : <p className="empty-note">No cross-provider relationship has enough normalized endpoints yet.</p>}
        </section>
        <section className="card col-6 unified-conflicts">
          <div className="card__head"><h2>Strong conflicts</h2></div>
          {state.conflicts.length ? (
            <ul>
              {state.conflicts.map((conflict) => (
                <li key={conflict.id}>
                  <span className="cross-state cross-state--bad">{conflict.field}</span>
                  <p>{conflict.summary}</p>
                </li>
              ))}
            </ul>
          ) : <p className="empty-note">No strong identifier conflict is retained.</p>}
        </section>
      </div>
    </div>
  );
}

function IdentityCandidate({
  link,
  left,
  right,
  busy,
  onDecision,
}: {
  link: IdentityLink;
  left: string;
  right: string;
  busy: boolean;
  onDecision: (linkId: string, decision: "confirm" | "reject" | "clear") => void;
}) {
  return (
    <article className={`unified-decision unified-decision--${stateTone(link.state)}`}>
      <div className="unified-decision__head">
        <div><strong>{left}</strong><span>↔</span><strong>{right}</strong></div>
        <span className={`cross-state cross-state--${stateTone(link.state)}`}>{link.state}</span>
      </div>
      <ul>
        {link.reasons.map((reason, index) => (
          <li key={`${reason.field}-${index}`}>
            <strong>{reason.strength} {reason.field}</strong> · {reason.summary}
          </li>
        ))}
      </ul>
      {link.state === "candidate" ? (
        <div className="settings-actions">
          <button className="btn btn--primary" disabled={busy} onClick={() => onDecision(link.id, "confirm")}>
            Confirm local identity
          </button>
          <button className="btn btn--ghost" disabled={busy} onClick={() => onDecision(link.id, "reject")}>
            Reject candidate
          </button>
        </div>
      ) : link.state === "rejected" || (link.state === "confirmed" && !link.automatic) ? (
        <button className="btn btn--ghost" disabled={busy} onClick={() => onDecision(link.id, "clear")}>
          {link.state === "confirmed" ? "Remove local confirmation" : "Reconsider candidate"}
        </button>
      ) : (
        <p className="unified-decision__boundary">Strong conflicts cannot be manually overridden.</p>
      )}
    </article>
  );
}
