"use client";

import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { GuideOperation, GuideRunResult, InterfaceStatus } from "@/lib/types";

const CATEGORIES: GuideOperation["category"][] = [
  "GETTING STARTED",
  "TROUBLESHOOTING",
  "NETWORKING",
  "SWITCH",
];

export default function LabGuide({
  operations,
  interfaces,
}: {
  operations: GuideOperation[];
  interfaces: InterfaceStatus[];
}) {
  const [selectedId, setSelectedId] = useState(operations[0]?.id || "");
  const [selectedInterface, setSelectedInterface] = useState(interfaces[0]?.port || "");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<GuideRunResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const selected = operations.find((operation) => operation.id === selectedId) || operations[0];
  const grouped = useMemo(
    () => CATEGORIES.map((category) => ({
      category,
      operations: operations.filter((operation) => operation.category === category),
    })),
    [operations],
  );

  async function run() {
    if (!selected) return;
    setRunning(true);
    setError(null);
    try {
      setResult(await api.runGuideOperation(
        selected.id,
        selected.requiresInterface ? selectedInterface : undefined,
      ));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRunning(false);
    }
  }

  if (!selected) {
    return <div className="card"><p className="empty-note">The command guide catalog is unavailable.</p></div>;
  }

  return (
    <section className="lab-guide" aria-labelledby="lab-guide-title">
      <aside className="card lab-guide__rail">
        <div className="eyebrow">Learn with your hardware</div>
        <h2 id="lab-guide-title">Command guide</h2>
        <p>Choose a question. SwitchOps selects a fixed safe operation and explains the parsed result.</p>
        <nav aria-label="Command guide operations">
          {grouped.map(({ category, operations: categoryOperations }) => (
            <div key={category} className="guide-group">
              <h3>{category}</h3>
              {categoryOperations.map((operation) => (
                <button
                  type="button"
                  key={operation.id}
                  className={operation.id === selected.id ? "is-active" : ""}
                  onClick={() => {
                    setSelectedId(operation.id);
                    setResult(null);
                    setError(null);
                  }}
                >
                  {operation.question}
                </button>
              ))}
            </div>
          ))}
        </nav>
      </aside>

      <div className="card lab-guide__workspace">
        <div className="guide-workspace__header">
          <div>
            <div className="eyebrow">{selected.category}</div>
            <h2>{selected.title}</h2>
            <p>{selected.whatItTellsYou}</p>
          </div>
          <span className="badge badge--green">{selected.safety}</span>
        </div>

        <div className="guide-safety-note">
          <strong>Safety boundary</strong>
          <span>This action cannot accept an IOS command. It resolves to the fixed command{selected.commands.length > 1 ? "s" : ""} shown below.</span>
        </div>

        {selected.requiresInterface ? (
          <label className="guide-interface-select">
            <span>Interface to inspect</span>
            <select value={selectedInterface} onChange={(event) => setSelectedInterface(event.target.value)}>
              {interfaces.map((item) => (
                <option value={item.port} key={item.port}>{item.port} · {item.name || item.status}</option>
              ))}
            </select>
          </label>
        ) : null}

        <details className="guide-commands">
          <summary>Cisco IOS commands</summary>
          <div>
            {selected.commands.map((command) => <code key={command}>{command}</code>)}
          </div>
        </details>

        <button
          type="button"
          className="btn btn--primary guide-run"
          onClick={run}
          disabled={running || (selected.requiresInterface && !selectedInterface)}
        >
          <span className="guide-run__icon" aria-hidden>▶</span>
          {running ? "Running read-only check…" : "Run read-only check"}
        </button>

        {error ? <div className="guide-result guide-result--error" role="alert">{error}</div> : null}
        {result ? (
          <div className="guide-result" aria-live="polite">
            <div className="guide-result__head">
              <div>
                <div className="eyebrow">Result</div>
                <strong>{result.explanation}</strong>
              </div>
              <time>{new Date(result.observedAt).toLocaleTimeString()}</time>
            </div>
            {result.warnings.map((warning) => (
              <p className="guide-result__warning" key={warning}>{warning}</p>
            ))}
            <StructuredResult value={result.result} />
          </div>
        ) : (
          <div className="guide-result guide-result--empty">
            The result will appear here as parsed data with a deterministic explanation.
          </div>
        )}
      </div>
    </section>
  );
}

function StructuredResult({ value }: { value: Record<string, unknown> }) {
  const summaryEntries = Object.entries(value).filter(([, item]) => (
    item == null || ["string", "number", "boolean"].includes(typeof item)
  ));
  return (
    <div className="structured-result">
      {summaryEntries.length ? (
        <dl>
          {summaryEntries.map(([key, item]) => (
            <div key={key}>
              <dt>{key.replace(/([A-Z])/g, " $1")}</dt>
              <dd>{item == null ? "—" : String(item)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      <details>
        <summary>Structured parsed data</summary>
        <pre>{JSON.stringify(value, null, 2)}</pre>
      </details>
    </div>
  );
}
