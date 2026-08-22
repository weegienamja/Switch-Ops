"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import type { SetupStatus } from "@/lib/types";

export default function SettingsPanel({
  setup,
  onClose,
  onChange,
}: {
  setup: SetupStatus;
  onClose: () => void;
  onChange: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function clearCreds() {
    if (
      !confirm(
        "Clear saved switch credentials? You will need to re-enter them on next launch.",
      )
    )
      return;
    setBusy(true);
    setError(null);
    try {
      await api.clearCredentials();
      onChange();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3 className="card__title" style={{ marginBottom: 14 }}>
          Settings
        </h3>
        <dl className="kv">
          <dt>Host</dt>
          <dd>{setup.switchHost || "—"}</dd>
          <dt>Username</dt>
          <dd>{setup.switchUsername || "—"}</dd>
          <dt>Device type</dt>
          <dd>{setup.switchDeviceType || "—"}</dd>
          <dt>Storage</dt>
          <dd>{setup.storage}</dd>
          <dt>Mock mode</dt>
          <dd>{setup.mockMode ? "yes" : "no"}</dd>
          <dt>Writes enabled</dt>
          <dd>{setup.enableWriteActions ? "yes" : "no"}</dd>
        </dl>
        {setup.storage === "file" ? (
          <div
            className="mono"
            style={{ color: "var(--amber)", fontSize: 12, marginTop: 14 }}
          >
            OS keyring unavailable. Credentials are in a restricted local fallback file.
          </div>
        ) : null}
        {error && (
          <div className="mono" style={{ color: "var(--red)", fontSize: 12, marginTop: 12 }}>
            {error}
          </div>
        )}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            gap: 10,
            marginTop: 20,
          }}
        >
          <button className="btn" disabled={busy} onClick={clearCreds}>
            Clear credentials
          </button>
          <button className="btn btn--primary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
