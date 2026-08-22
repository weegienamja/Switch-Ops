"use client";

import type { ReconciliationSummary } from "@/lib/types";
import { STATUS_COPY, summaryTone } from "@/lib/reconciliation";

/**
 * Topology reconciliation on Overview, deliberately beside health rather than
 * inside it. A network can be entirely healthy and still not match what the
 * operator believes is plugged in, and the two must be readable separately.
 */
export default function ReconciliationSummaryPanel({
  reconciliation,
  onInspect,
}: {
  reconciliation: ReconciliationSummary;
  onInspect?: (port: string) => void;
}) {
  const tone = summaryTone(reconciliation.attention, reconciliation.uncertain);
  const tracked = reconciliation.interfaces.filter(
    (item) => item.status !== "not-applicable",
  );
  const needsDecision = tracked.filter((item) =>
    ["drift", "expected-not-observed", "unexpected"].includes(item.status),
  );

  const counts: Array<{ key: string; label: string; value: number }> = [
    { key: "aligned", label: "Aligned", value: reconciliation.aligned },
    { key: "drift", label: "Drift", value: reconciliation.drift },
    { key: "missing", label: "Not observed", value: reconciliation.expectedNotObserved },
    { key: "unexpected", label: "Unrecorded", value: reconciliation.unexpected },
    { key: "uncertain", label: "Unconfirmed", value: reconciliation.uncertain },
  ].filter((item) => item.value > 0);

  return (
    <section className="card reconciliation-summary" aria-labelledby="reconciliation-summary-title">
      <div className="card__head">
        <div>
          <h2 className="card__title" id="reconciliation-summary-title">
            Topology reconciliation
          </h2>
          <div className="card__subtitle">
            Does what SwitchOps observed match what you expect to be here? Separate from health.
          </div>
        </div>
        <span className={`state-chip state-chip--${tone === "ok" ? "good" : tone === "warn" ? "warn" : "info"}`}>
          <i aria-hidden />
          {reconciliation.attention ? "Attention" : tone === "info" ? "Unconfirmed" : "Aligned"}
        </span>
      </div>

      <p className="reconciliation-summary__headline">{reconciliation.headline}</p>

      {counts.length ? (
        <dl className="reconciliation-counts">
          {counts.map((item) => (
            <div key={item.key} className={`reconciliation-counts__item is-${item.key}`}>
              <dt>{item.label}</dt>
              <dd>{item.value}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="empty-note">No topology intent has been recorded yet.</p>
      )}

      {needsDecision.length ? (
        <ul className="reconciliation-list">
          {needsDecision.map((item) => (
            <li key={item.interface}>
              <button
                type="button"
                className="reconciliation-list__row"
                onClick={() => onInspect?.(item.interface)}
              >
                <span className="reconciliation-list__port">{item.interface}</span>
                <span className={`recon-badge recon-badge--${item.status}`}>
                  {STATUS_COPY[item.status].label}
                </span>
                <span className="reconciliation-list__headline">{item.headline}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      <p className="empty-note reconciliation-summary__note">
        Health describes whether the switch and its links are working. Reconciliation describes
        whether the network matches your documented intent. Both can be true at once.
      </p>
    </section>
  );
}
