import type { HealthAssessment } from "@/lib/types";

export default function HealthPanel({ health }: { health: HealthAssessment }) {
  return (
    <section className={`card health-panel health-panel--${health.state.toLowerCase()}`}>
      <div className="card__head">
        <div>
          <div className="eyebrow">Current health</div>
          <h2 className="health-panel__state">{health.state}</h2>
        </div>
        <span className="badge">
          {health.basedOnHistory ? "compared with previous observation" : "first observation"}
        </span>
      </div>
      <ul className="health-reasons">
        {health.reasons.map((reason) => (
          <li key={`${reason.code}-${reason.interface || "device"}`}>
            <span className={`health-reason-mark health-reason-mark--${reason.severity.toLowerCase()}`} aria-hidden />
            <div>
              <strong>{reason.title}</strong>
              <p>{reason.detail}</p>
            </div>
            {reason.interface ? <span className="badge">{reason.interface}</span> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
