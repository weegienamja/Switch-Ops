"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import type { BackupResult } from "@/lib/types";
import { formatBytes } from "@/lib/format";

export default function ConfigBackupPanel({
  onChange,
}: {
  onChange: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BackupResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.backup();
      setResult(r);
      onChange();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <div className="card__head">
        <div>
          <h3 className="card__title">Config backup</h3>
          <div className="card__subtitle">
            Saves the sensitive running configuration locally; only this preview is redacted.
          </div>
        </div>
        <button className="btn btn--primary" onClick={run} disabled={busy}>
          {busy ? "Backing up…" : "Run backup"}
        </button>
      </div>
      {error && (
        <div className="mono" style={{ color: "var(--red)", fontSize: 12 }}>
          {error}
        </div>
      )}
      {result && (
        <>
          <dl className="kv" style={{ marginBottom: 14 }}>
            <dt>File</dt>
            <dd className="mono">{result.filename}</dd>
            <dt>Size</dt>
            <dd>{formatBytes(result.sizeBytes)}</dd>
            <dt>Timestamp</dt>
            <dd>{new Date(result.timestamp).toLocaleString()}</dd>
          </dl>
          <div className="pre">{result.redactedPreview}</div>
        </>
      )}
    </div>
  );
}
