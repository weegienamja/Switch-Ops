"use client";
export default function LoadingState() {
  return (
    <div className="app-shell">
      <div className="container">
        <div className="card" style={{ textAlign: "center", padding: 48 }}>
          <div className="mono" style={{ fontSize: 11, letterSpacing: 1, color: "var(--text-muted)" }}>
            CONNECTING TO BACKEND
          </div>
          <div style={{ marginTop: 12, fontSize: 18 }}>Loading switch state…</div>
          <div className="pulse" style={{ margin: "20px auto 0" }} />
        </div>
      </div>
    </div>
  );
}
