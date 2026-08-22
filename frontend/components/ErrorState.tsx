"use client";
export default function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="app-shell">
      <div className="container">
        <div className="card" style={{ borderColor: "rgba(239,68,68,0.35)" }}>
          <h3 className="card__title" style={{ color: "var(--red)" }}>
            Connection failed
          </h3>
          <div className="mono" style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 16 }}>
            {message}
          </div>
          <div style={{ display: "flex", gap: 10 }}>
            {onRetry && (
              <button className="btn btn--primary" onClick={onRetry}>
                Retry
              </button>
            )}
            <div style={{ color: "var(--text-dim)", fontSize: 12, marginTop: 8 }}>
              Ensure the backend sidecar is running on 127.0.0.1:8765.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
