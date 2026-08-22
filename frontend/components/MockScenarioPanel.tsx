"use client";

export default function MockScenarioPanel({
  scenario,
  busy,
  onChange,
}: {
  scenario: "baseline" | "ap_attached";
  busy: boolean;
  onChange: (scenario: "baseline" | "ap_attached") => void;
}) {
  return (
    <section className="mock-scenario" aria-label="Mock device attachment demonstration">
      <div>
        <span className="badge badge--cyan">SIMULATION · MOCK ONLY</span>
        <strong>Attachment demonstration</strong>
        <p>
          Exercise the “waiting → link → PoE → learned device” workflow with synthetic telemetry. No physical switch command is sent.
        </p>
      </div>
      <div className="mock-scenario__controls">
        <button
          type="button"
          className={`btn ${scenario === "baseline" ? "btn--primary" : ""}`}
          disabled={busy}
          onClick={() => onChange("baseline")}
        >
          Reset to waiting
        </button>
        <button
          type="button"
          className={`btn ${scenario === "ap_attached" ? "btn--primary" : ""}`}
          disabled={busy}
          onClick={() => onChange("ap_attached")}
        >
          Simulate AP attached
        </button>
      </div>
    </section>
  );
}
