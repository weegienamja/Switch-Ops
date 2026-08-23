"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type {
  ConnectionTestResult,
  InterfacePolicyResponse,
  InterfacePolicyState,
  InterfaceStatus,
  RuntimeInfo,
  SetupStatus,
} from "@/lib/types";

/** Plain-English names for the credential backends. Never show "keyring". */
const STORAGE_LABEL: Record<SetupStatus["storage"], string> = {
  keyring: "Windows Credential Manager",
  file: "Restricted local file",
  env: "Environment variable",
  none: "Not stored",
};

const STORAGE_DETAIL: Record<SetupStatus["storage"], string> = {
  keyring: "Stored in the Windows secure credential store, encrypted for your user account.",
  file: "The OS credential store was unavailable, so credentials are in a permission-restricted local file.",
  env: "Read from an environment variable in this session. Nothing is written to disk.",
  none: "No switch credentials have been saved yet.",
};

/** Driver id to the platform name a beginner would recognise. */
const PLATFORM_LABEL: Record<string, string> = {
  cisco_ios: "Cisco IOS",
};

function friendlyPath(path: string | undefined): string {
  if (!path) return "—";
  // %LOCALAPPDATA% is long and noisy; keep the meaningful tail.
  const match = /^[A-Za-z]:\\Users\\[^\\]+\\(.*)$/.exec(path);
  return match ? `…\\${match[1]}` : path;
}

export default function SettingsPanel({
  setup,
  interfaces = [],
  onClose,
  onChange,
}: {
  setup: SetupStatus;
  interfaces?: InterfaceStatus[];
  onClose: () => void;
  onChange: () => void;
}) {
  const [info, setInfo] = useState<RuntimeInfo | null>(null);
  const [policy, setPolicy] = useState<InterfacePolicyResponse | null>(null);
  const [policyDrafts, setPolicyDrafts] = useState<Record<string, InterfacePolicyState>>({});
  const [policyBusy, setPolicyBusy] = useState<string | null>(null);
  const [policyError, setPolicyError] = useState<string | null>(null);
  const [confirmWrites, setConfirmWrites] = useState(false);
  const [test, setTest] = useState<ConnectionTestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [testError, setTestError] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    void api
      .systemInfo()
      .then((value) => {
        if (!cancelled) setInfo(value);
      })
      .catch(() => {
        // Settings still works without the extra runtime facts.
      });
    void api
      .interfacePolicy()
      .then((value) => {
        if (!cancelled) acceptPolicy(value);
      })
      .catch((cause) => {
        if (!cancelled) {
          setPolicyError(cause instanceof Error ? cause.message : String(cause));
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function acceptPolicy(value: InterfacePolicyResponse) {
    setPolicy(value);
    setPolicyDrafts(
      Object.fromEntries(value.interfaces.map((entry) => [entry.interface, entry.state])),
    );
  }

  async function setControlledWrites(enabled: boolean) {
    setPolicyBusy("control");
    setPolicyError(null);
    try {
      acceptPolicy(await api.setControlledWrites(enabled));
      setConfirmWrites(false);
    } catch (cause) {
      setPolicyError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPolicyBusy(null);
    }
  }

  async function applyInterfacePolicy(interfaceName: string) {
    const state = policyDrafts[interfaceName];
    if (!state) return;
    setPolicyBusy(interfaceName);
    setPolicyError(null);
    try {
      acceptPolicy(await api.setInterfacePolicy(interfaceName, state));
    } catch (cause) {
      setPolicyError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPolicyBusy(null);
    }
  }

  useEffect(() => {
    dialogRef.current?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function runTest() {
    setTesting(true);
    setTestError(null);
    setTest(null);
    try {
      setTest(await api.testConnection());
    } catch (cause) {
      setTestError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setTesting(false);
    }
  }

  async function clearCredentials() {
    setBusy(true);
    setError(null);
    try {
      await api.clearCredentials();
      setConfirmClear(false);
      onChange();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  const platform = PLATFORM_LABEL[setup.switchDeviceType || ""] || "Unrecognised platform";

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="settings-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        tabIndex={-1}
        ref={dialogRef}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="settings-dialog__head">
          <div>
            <div className="eyebrow">SwitchOps {info?.version || ""}</div>
            <h2 id="settings-title">Settings</h2>
          </div>
          <button className="btn btn--ghost" onClick={onClose} aria-label="Close settings">
            Close
          </button>
        </header>

        <div className="settings-dialog__body">
          <Section title="Device connection">
            <div className="settings-device">
              <div>
                <strong>{setup.switchHost ? `Switch at ${setup.switchHost}` : "No switch configured"}</strong>
                <span>{platform}</span>
              </div>
              <StateChip
                tone={setup.configured ? "good" : "warn"}
                label={setup.configured ? "Credentials saved" : "Not configured"}
              />
            </div>
            <Rows
              rows={[
                { label: "Host", value: setup.switchHost || "—" },
                { label: "Username", value: setup.switchUsername || "—" },
                { label: "Platform", value: platform },
                {
                  label: "Credential storage",
                  value: STORAGE_LABEL[setup.storage],
                  hint: STORAGE_DETAIL[setup.storage],
                },
              ]}
            />
            <div className="settings-actions">
              <button className="btn btn--primary" onClick={runTest} disabled={testing}>
                {testing ? "Testing…" : "Test connection"}
              </button>
              <span className="settings-actions__note">
                Read-only. Sends only allowlisted show commands and changes nothing.
              </span>
            </div>
            {testError ? (
              <p className="settings-alert settings-alert--bad" role="alert">{testError}</p>
            ) : null}
            {test ? <ConnectionTestReport result={test} /> : null}
          </Section>

          <Section title="Operation mode">
            <div className="settings-modes">
              <ModeCard
                label="Device connection"
                state={setup.configured ? "Configured" : "Not configured"}
                tone={setup.configured ? "good" : "neutral"}
              />
              <ModeCard
                label="Write operations"
                state={policy?.controlledWritesEnabled ? "Enabled" : "Disabled"}
                tone={policy?.controlledWritesEnabled ? "warn" : "good"}
              />
            </div>
            <p className="settings-explain">
              {policy?.controlledWritesEnabled
                ? "Bounded changes can be unlocked for this process, but only on interfaces explicitly marked OPERABLE."
                : "SwitchOps may inspect this device, but controlled writes are globally disabled."}
            </p>
          </Section>

          <Section title="Interface write policy">
            <p className="settings-explain">
              Policy is stored only on this PC and is scoped to a one-way hash of the configured
              device address. New devices and interfaces start UNMANAGED. The backend enforces
              these states even if the interface is manipulated in the UI.
            </p>
            {policy && !policy.valid ? (
              <p className="settings-alert settings-alert--bad" role="alert">
                The local policy is invalid and SwitchOps has failed closed to read-only.
                {policy.loadError ? ` ${policy.loadError}` : ""}
              </p>
            ) : null}
            <div className="settings-danger__row interface-policy-control">
              <div>
                <strong>Controlled writes</strong>
                <span>
                  {policy?.controlledWritesEnabled
                    ? "Enabled locally. Each process still starts locked."
                    : "Off by default. All device changes are blocked."}
                </span>
              </div>
              {policy?.controlledWritesEnabled ? (
                <button
                  className="btn btn--danger"
                  disabled={policyBusy === "control"}
                  onClick={() => void setControlledWrites(false)}
                >
                  Disable controlled writes
                </button>
              ) : confirmWrites ? (
                <div className="settings-actions">
                  <button className="btn btn--ghost" onClick={() => setConfirmWrites(false)}>
                    Cancel
                  </button>
                  <button
                    className="btn btn--danger"
                    disabled={policyBusy === "control" || !policy?.valid}
                    onClick={() => void setControlledWrites(true)}
                  >
                    Confirm enable
                  </button>
                </div>
              ) : (
                <button
                  className="btn"
                  disabled={!policy?.valid}
                  onClick={() => setConfirmWrites(true)}
                >
                  Review enabling writes
                </button>
              )}
            </div>
            {confirmWrites && !policy?.controlledWritesEnabled ? (
              <p className="settings-alert settings-alert--bad" role="alert">
                Enabling this capability permits bounded configuration changes after a separate
                session unlock. It does not make UNMANAGED or PROTECTED interfaces operable.
              </p>
            ) : null}
            <div className="interface-policy-list">
              {(policy?.interfaces || []).map((entry) => {
                const observed = interfaces.find((item) => item.port === entry.interface);
                const draft = policyDrafts[entry.interface] || entry.state;
                const physical = /^(Fa|Gi|Te|Twe|Fo|Hu)/.test(entry.interface);
                return (
                  <div className="interface-policy-row" key={entry.interface}>
                    <div>
                      <strong>{entry.interface}</strong>
                      <span>{observed?.name || observed?.status || "Known to local policy"}</span>
                    </div>
                    <select
                      aria-label={`Policy for ${entry.interface}`}
                      value={draft}
                      onChange={(event) =>
                        setPolicyDrafts((current) => ({
                          ...current,
                          [entry.interface]: event.target.value as InterfacePolicyState,
                        }))
                      }
                    >
                      <option value="UNMANAGED">UNMANAGED</option>
                      <option value="PROTECTED">PROTECTED</option>
                      <option value="OPERABLE" disabled={!physical}>OPERABLE</option>
                    </select>
                    <button
                      className="btn btn--ghost"
                      disabled={draft === entry.state || policyBusy === entry.interface || !policy?.valid}
                      onClick={() => void applyInterfacePolicy(entry.interface)}
                    >
                      {policyBusy === entry.interface ? "Applying…" : "Apply"}
                    </button>
                  </div>
                );
              })}
            </div>
            {!policy && !policyError ? <p className="settings-explain">Loading local policy…</p> : null}
            {policyError ? <p className="settings-alert settings-alert--bad" role="alert">{policyError}</p> : null}
          </Section>

          <Section title="Security">
            <Rows
              rows={[
                { label: "Backend", value: "Localhost only", hint: `Bound to ${info?.apiHost || "127.0.0.1"}; not reachable from the network.` },
                {
                  label: "SSH host key",
                  value: info ? (info.hostKeyPinned ? "Pinned" : "Not yet pinned") : "—",
                  hint: info?.hostKeyPinned
                    ? "The switch key was recorded on first use. A change is refused rather than accepted."
                    : "The first successful connection will record the key and refuse changes afterwards.",
                },
                {
                  label: "Credentials",
                  value: STORAGE_LABEL[setup.storage],
                  hint: "Never written to logs, backups, or API responses.",
                },
                { label: "Raw CLI", value: "Unavailable", hint: "No endpoint accepts an arbitrary IOS command." },
                {
                  label: "API documentation",
                  value: info?.apiDocsEnabled ? "Enabled" : "Disabled",
                },
                {
                  label: "Legacy SSH compatibility",
                  value: info?.legacySsh ? "Enabled for this process" : "Disabled",
                  hint: "Older ciphers are permitted inside SwitchOps only. Windows SSH configuration is untouched.",
                },
              ]}
            />
          </Section>

          <Section title="Data">
            <Rows
              rows={[
                {
                  label: "Telemetry collection",
                  value: "Tiered live",
                  hint: "Ports update every few seconds; costlier health and discovery checks run on slower bounded tiers.",
                },
                { label: "Telemetry retention", value: `${info?.telemetryRetentionDays ?? 30} days` },
                { label: "Configuration history", value: "Local only", hint: "Raw configurations stay in private local files; the UI shows redacted diffs." },
                { label: "Backups", value: friendlyPath(info?.backupDir) },
                { label: "Database and logs", value: friendlyPath(info?.dataDir) },
              ]}
            />
          </Section>

          <details className="settings-advanced">
            <summary>Advanced</summary>
            <Rows
              rows={[
                { label: "Device driver", value: info?.deviceDriver || setup.switchDeviceType || "—" },
                { label: "Backend API", value: info ? `${info.apiHost}:${info.apiPort}` : "—" },
                { label: "Allowed origins", value: (info?.corsOrigins || []).join(", ") || "—" },
                { label: "Log directory", value: info?.logDir || "—" },
                { label: "Application version", value: info?.version || "—" },
              ]}
            />
          </details>

          <section className="settings-danger">
            <h3>Danger zone</h3>
            {confirmClear ? (
              <div className="settings-danger__confirm" role="alertdialog" aria-label="Confirm clearing credentials">
                <p>
                  This removes the stored SwitchOps login from {STORAGE_LABEL[setup.storage]}. The switch
                  configuration will not be changed, and you will need to enter the credentials again
                  on the next launch.
                </p>
                <div className="settings-actions">
                  <button className="btn btn--ghost" onClick={() => setConfirmClear(false)} disabled={busy}>
                    Cancel
                  </button>
                  <button className="btn btn--danger" onClick={clearCredentials} disabled={busy}>
                    {busy ? "Clearing…" : "Yes, clear credentials"}
                  </button>
                </div>
              </div>
            ) : (
              <div className="settings-danger__row">
                <div>
                  <strong>Clear stored credentials</strong>
                  <span>Removes the saved login. The switch itself is not modified.</span>
                </div>
                <button
                  className="btn btn--danger"
                  onClick={() => setConfirmClear(true)}
                  disabled={!setup.configured}
                >
                  Clear credentials
                </button>
              </div>
            )}
            {error ? <p className="settings-alert settings-alert--bad" role="alert">{error}</p> : null}
          </section>
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="settings-section">
      <h3 className="settings-section__title">{title}</h3>
      {children}
    </section>
  );
}

function Rows({ rows }: { rows: Array<{ label: string; value: string; hint?: string }> }) {
  return (
    <dl className="settings-rows">
      {rows.map((row) => (
        <div key={row.label}>
          <dt>{row.label}</dt>
          <dd>
            <span>{row.value}</span>
            {row.hint ? <small>{row.hint}</small> : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function StateChip({ tone, label }: { tone: "good" | "warn" | "info" | "neutral"; label: string }) {
  return (
    <span className={`state-chip state-chip--${tone}`}>
      <i aria-hidden />
      {label}
    </span>
  );
}

function ModeCard({
  label,
  state,
  tone,
}: {
  label: string;
  state: string;
  tone: "good" | "warn" | "info" | "neutral";
}) {
  return (
    <div className={`mode-card mode-card--${tone}`}>
      <span className="mode-card__label">{label}</span>
      <StateChip tone={tone} label={state} />
    </div>
  );
}

const CHECK_MARK: Record<ConnectionTestResult["checks"][number]["status"], string> = {
  pass: "PASS",
  fail: "FAIL",
  skipped: "SKIP",
};

function ConnectionTestReport({ result }: { result: ConnectionTestResult }) {
  return (
    <div className={`conn-test conn-test--${result.ok ? "ok" : "bad"}`} aria-live="polite">
      <div className="conn-test__head">
        <strong>Connection test</strong>
        <span>{new Date(result.testedAt).toLocaleTimeString()}</span>
      </div>
      <p className="conn-test__summary">{result.summary}</p>
      <ul className="conn-test__checks">
        {result.checks.map((check) => (
          <li key={check.id} className={`conn-check conn-check--${check.status}`}>
            <span className="conn-check__mark">{CHECK_MARK[check.status]}</span>
            <span className="conn-check__copy">
              <strong>{check.label}</strong>
              <small>{check.detail}</small>
            </span>
          </li>
        ))}
      </ul>
      <p className="conn-test__boundary">
        This test does not check Internet access, privilege level, or switch health. It only reports
        what it observed.
      </p>
    </div>
  );
}
