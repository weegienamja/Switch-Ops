"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { ConfigurationHistoryEntry } from "@/lib/types";

export default function ConfigurationHistoryPanel({
  entries,
  onChange,
}: {
  entries: ConfigurationHistoryEntry[];
  onChange: () => void;
}) {
  const [marking, setMarking] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function markKnownGood(id: number) {
    setMarking(id);
    setError(null);
    try {
      await api.markConfigurationKnownGood(id);
      onChange();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setMarking(null);
    }
  }

  return (
    <section className="card config-history" aria-labelledby="config-history-title">
      <div className="card__head">
        <div>
          <h2 className="card__title" id="config-history-title">Configuration history</h2>
          <div className="card__subtitle">
            Change-only local versions from the running configuration already collected by the dashboard.
          </div>
        </div>
        <span className="badge">{entries.length} version{entries.length === 1 ? "" : "s"}</span>
      </div>
      {error ? <p className="guide-result__warning">{error}</p> : null}
      {entries.length ? (
        <ol className="config-version-list">
          {entries.map((entry, index) => (
            <li key={entry.id}>
              <div className="config-version__rail">
                <span className={entry.changeDetected ? "has-change" : ""} />
                {index < entries.length - 1 ? <i /> : null}
              </div>
              <div className="config-version__content">
                <div className="config-version__head">
                  <div>
                    <strong>{entry.changeDetected ? "Configuration drift observed" : "Initial configuration observation"}</strong>
                    <time>{new Date(entry.timestamp).toLocaleString()}</time>
                  </div>
                  <div>
                    {entry.knownGood ? <span className="badge badge--green">known good</span> : (
                      <button
                        type="button"
                        className="btn btn--ghost"
                        disabled={marking != null}
                        onClick={() => void markKnownGood(entry.id)}
                      >
                        {marking === entry.id ? "Marking…" : "Mark known good"}
                      </button>
                    )}
                  </div>
                </div>
                <div className="config-version__meta">
                  <code>{entry.fingerprint.slice(0, 12)}</code>
                  <span>{entry.filename}</span>
                  <span>{entry.source === "external_or_unknown" ? "Change source unknown / external to SwitchOps" : "Baseline captured"}</span>
                </div>
                {entry.redactedDiff.length ? (
                  <details className="config-diff">
                    <summary>Show redacted line diff · {entry.redactedDiff.length} lines</summary>
                    <pre>{entry.redactedDiff.join("\n")}</pre>
                  </details>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <p className="empty-note">No configuration version is available. A successful aggregate observation establishes the first baseline.</p>
      )}
      <p className="config-history__safety">
        Raw configurations remain in private local files. This view exposes only fingerprints, filenames, and redacted diffs.
      </p>
    </section>
  );
}
