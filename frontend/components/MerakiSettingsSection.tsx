"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type {
  MerakiConnectionTestResult,
  MerakiNetwork,
  MerakiOrganization,
  MerakiSetupStatus,
} from "@/lib/unifiedTypes";

export default function MerakiSettingsSection() {
  const [status, setStatus] = useState<MerakiSetupStatus | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [keyEntryOpen, setKeyEntryOpen] = useState(false);
  const [organizations, setOrganizations] = useState<MerakiOrganization[]>([]);
  const [networks, setNetworks] = useState<MerakiNetwork[]>([]);
  const [organizationId, setOrganizationId] = useState("");
  const [networkId, setNetworkId] = useState("");
  const [test, setTest] = useState<MerakiConnectionTestResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void api.merakiStatus()
      .then((value) => {
        if (cancelled) return;
        setStatus(value);
        setOrganizationId(value.selection?.organizationId || "");
        setNetworkId(value.selection?.networkId || "");
      })
      .catch(() => {
        // Catalyst Settings remains fully usable when this optional source is absent.
      });
    return () => { cancelled = true; };
  }, []);

  async function saveKey() {
    if (!apiKey.trim()) return;
    setBusy("key");
    setError(null);
    try {
      setStatus(await api.saveMerakiApiKey(apiKey.trim()));
      setApiKey("");
      setKeyEntryOpen(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  async function runTest() {
    setBusy("test");
    setError(null);
    setTest(null);
    try {
      setTest(await api.testMerakiConnection());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  async function loadOrganizations() {
    setBusy("organizations");
    setError(null);
    try {
      const values = await api.merakiOrganizations();
      setOrganizations(values);
      const selected = organizationId || status?.selection?.organizationId || values[0]?.id || "";
      setOrganizationId(selected);
      if (selected) await loadNetworks(selected);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  async function loadNetworks(selectedOrganizationId: string) {
    if (!selectedOrganizationId) {
      setNetworks([]);
      setNetworkId("");
      return;
    }
    setBusy("networks");
    setError(null);
    try {
      const values = await api.merakiNetworks(selectedOrganizationId);
      setNetworks(values);
      setNetworkId((current) =>
        values.some((item) => item.id === current) ? current : values[0]?.id || "",
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  async function saveSelection() {
    const organization = organizations.find((item) => item.id === organizationId);
    const network = networks.find((item) => item.id === networkId);
    if (!organization || !network) return;
    setBusy("selection");
    setError(null);
    try {
      setStatus(await api.saveMerakiSelection({
        organizationId: organization.id,
        organizationName: organization.name,
        networkId: network.id,
        networkName: network.name,
      }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  async function clearKey() {
    setBusy("clear");
    setError(null);
    try {
      setStatus(await api.clearMerakiApiKey());
      setTest(null);
      setOrganizations([]);
      setNetworks([]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="settings-section meraki-settings">
      <h3 className="settings-section__title">Meraki evidence source · optional</h3>
      <div className="settings-device">
        <div>
          <strong>{status?.selection?.networkName || "No Meraki network selected"}</strong>
          <span>
            {status?.configured
              ? "API key stored in Windows Credential Manager"
              : "No Dashboard API key stored"}
          </span>
        </div>
        <span className={`state-chip state-chip--${status?.sourceHealth.state === "healthy" ? "good" : status?.configured ? "warn" : "neutral"}`}>
          <i aria-hidden />
          {status?.sourceHealth.state.replaceAll("-", " ") || "Not configured"}
        </span>
      </div>
      <p className="settings-explain">
        This optional source uses a fixed allowlist of Dashboard API GET operations. It has no generic
        proxy and no Meraki write path. Losing Meraki access does not interrupt Catalyst operation.
      </p>
      {!status ? (
        <p className="settings-explain">Loading optional source status…</p>
      ) : !status.configured && keyEntryOpen ? (
        <div className="meraki-key-row">
          <label>
            <span>Dashboard API key</span>
            <input
              type="password"
              value={apiKey}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder="Stored only after you choose Save key"
            />
          </label>
          <button className="btn btn--primary" onClick={() => void saveKey()} disabled={!apiKey.trim() || busy === "key" || status?.keyringAvailable === false}>
            {busy === "key" ? "Saving…" : "Save key"}
          </button>
        </div>
      ) : !status.configured ? (
        <div className="settings-actions">
          <button
            className="btn"
            onClick={() => setKeyEntryOpen(true)}
            disabled={!status.keyringAvailable}
          >
            Add Meraki evidence source
          </button>
        </div>
      ) : (
        <div className="settings-actions">
          <button className="btn btn--primary" onClick={() => void runTest()} disabled={busy !== null}>
            {busy === "test" ? "Testing…" : "Test read-only connection"}
          </button>
          <button className="btn" onClick={() => void loadOrganizations()} disabled={busy !== null}>
            {busy === "organizations" || busy === "networks" ? "Loading scope…" : "Choose organization and network"}
          </button>
          <button className="btn btn--ghost" onClick={() => void clearKey()} disabled={busy !== null}>
            {busy === "clear" ? "Clearing…" : "Clear Meraki key"}
          </button>
        </div>
      )}
      {test ? (
        <div className={`conn-test conn-test--${test.ok ? "ok" : "bad"}`}>
          <div className="conn-test__head"><strong>Meraki connection</strong><span>{new Date(test.checkedAt).toLocaleTimeString()}</span></div>
          <p className="conn-test__summary">{test.summary}</p>
          <p className="conn-test__boundary">Organizations visible: {test.organizationsVisible}. No topology model was changed by this test.</p>
        </div>
      ) : null}
      {organizations.length ? (
        <div className="meraki-scope-grid">
          <label>
            <span>Organization</span>
            <select
              value={organizationId}
              onChange={(event) => {
                const value = event.target.value;
                setOrganizationId(value);
                void loadNetworks(value);
              }}
            >
              {organizations.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label>
            <span>Network</span>
            <select value={networkId} onChange={(event) => setNetworkId(event.target.value)}>
              {networks.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
            </select>
          </label>
          <button className="btn btn--primary" onClick={() => void saveSelection()} disabled={!networkId || busy !== null}>
            {busy === "selection" ? "Saving…" : "Use this network"}
          </button>
        </div>
      ) : null}
      {status?.selection ? (
        <p className="settings-actions__note">
          Selected locally: {status.selection.organizationName} · {status.selection.networkName}
        </p>
      ) : null}
      {status?.keyringAvailable === false ? (
        <p className="settings-alert settings-alert--bad" role="alert">
          Windows Credential Manager is unavailable. SwitchOps will not fall back to a file or environment variable for this key.
        </p>
      ) : null}
      {error ? <p className="settings-alert settings-alert--bad" role="alert">{error}</p> : null}
    </section>
  );
}
