"use client";
import { useState } from "react";
import { api } from "@/lib/api";

export default function SetupWizard({
  onComplete,
}: {
  onComplete: () => void;
}) {
  const [form, setForm] = useState({
    switchHost: "192.0.2.190",
    switchUsername: "operator",
    switchPassword: "",
    switchEnableSecret: "",
    switchDeviceType: "cisco_ios",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function update<K extends keyof typeof form>(key: K, value: string) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.saveCredentials(form);
      onComplete();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app-shell">
      <div className="warning-banner">
        First-run setup · credentials are stored locally in the OS keyring when available
      </div>
      <div className="container" style={{ maxWidth: 640 }}>
        <header className="header">
          <div className="header__brand">
            <div className="header__logo">JLS</div>
            <div>
              <div className="header__title">SwitchOps</div>
              <div className="header__subtitle">setup wizard</div>
            </div>
          </div>
        </header>

        <form className="card" onSubmit={submit}>
          <h3 className="card__title">Switch credentials</h3>
          <div className="card__subtitle" style={{ marginBottom: 18 }}>
            Local-only. Credentials never leave this machine. Legacy SSH compatibility is
            isolated to the SwitchOps backend process.
          </div>

          {(
            [
              ["switchHost", "Switch host", "192.0.2.190"],
              ["switchUsername", "Username", "operator"],
              ["switchPassword", "Password", "•••"],
              ["switchEnableSecret", "Enable secret", "•••"],
              ["switchDeviceType", "Device type", "cisco_ios"],
            ] as const
          ).map(([key, label, placeholder]) => (
            <div key={key} style={{ marginBottom: 14 }}>
              <label className="label" htmlFor={key}>
                {label}
              </label>
              <input
                id={key}
                className="input"
                type={
                  key === "switchPassword" || key === "switchEnableSecret"
                    ? "password"
                    : "text"
                }
                value={(form as any)[key]}
                placeholder={placeholder}
                onChange={(e) => update(key, e.target.value)}
                autoComplete="off"
                required={key !== "switchEnableSecret"}
              />
            </div>
          ))}

          {error && (
            <div
              className="mono"
              style={{ color: "var(--red)", fontSize: 12, marginBottom: 14 }}
            >
              {error}
            </div>
          )}

          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div className="mono" style={{ fontSize: 11, color: "var(--text-dim)" }}>
              Saved password is never displayed back.
            </div>
            <button className="btn btn--primary" type="submit" disabled={busy}>
              {busy ? "Saving…" : "Save and connect"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
