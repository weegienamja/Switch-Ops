"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type {
  DeviceType,
  ExpectedRelationship,
  InterfaceReconciliation,
  TopologyAssertion,
} from "@/lib/types";
import {
  EVIDENCE_CLASS_COPY,
  SOURCE_COPY,
  STATUS_COPY,
  assertionsByClass,
} from "@/lib/reconciliation";

const DEVICE_TYPE_OPTIONS: DeviceType[] = [
  "unknown",
  "router",
  "switch",
  "access-point",
  "desktop",
  "laptop",
  "server",
  "phone",
  "tv-media",
  "printer",
  "camera",
];

/**
 * Everything SwitchOps holds about one interface, grouped by what kind of
 * knowledge each claim is, plus the actions that change SwitchOps' own record
 * of intent.
 *
 * None of these actions touch the switch. Recording an expectation is local
 * metadata; when it disagrees with the description configured on the device,
 * that disagreement is reported rather than silently corrected.
 */
export default function ReconciliationInspector({
  deviceId,
  result,
  intent,
  onIntentChange,
}: {
  deviceId: string;
  result: InterfaceReconciliation | undefined;
  intent?: ExpectedRelationship;
  onIntentChange: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [deviceType, setDeviceType] = useState<DeviceType>("unknown");

  useEffect(() => {
    setEditing(false);
    setError(null);
    setName(intent?.expectedName || result?.expected?.objectLabel || "");
    setDeviceType(intent?.expectedDeviceType || "unknown");
  }, [result?.interface, intent?.expectedName, intent?.expectedDeviceType, result?.expected?.objectLabel]);

  if (!result) {
    return (
      <p className="empty-note">
        This interface was not part of the last reconciliation.
      </p>
    );
  }

  const copy = STATUS_COPY[result.status];
  const groups = assertionsByClass(result);
  const observedName =
    result.observed?.objectIdentified ? result.observed.objectLabel : null;

  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      onIntentChange();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="reconciliation-inspector">
      <div className="reconciliation-inspector__head">
        <span className={`recon-badge recon-badge--${result.status}`}>{copy.label}</span>
        <strong>{result.headline}</strong>
      </div>
      <p className="reconciliation-inspector__explanation">{result.explanation}</p>

      {result.changedSincePrevious && result.changeSummary ? (
        <p className="reconciliation-inspector__changed">
          <span className="recon-badge recon-badge--changed">Changed</span>
          {result.changeSummary}
        </p>
      ) : null}

      {result.documentationStale ? (
        <p className="reconciliation-inspector__stale">
          The switch&apos;s own interface description still reflects the older topology.
          SwitchOps has not changed it — configuration writes are disabled.
        </p>
      ) : null}

      {groups.map((group) => (
        <section key={group.evidenceClass} className="assertion-group">
          <div className={`assertion-group__label assertion-group__label--${group.evidenceClass}`}>
            {EVIDENCE_CLASS_COPY[group.evidenceClass].label}
          </div>
          <ul>
            {group.assertions.map((assertion, index) => (
              <AssertionRow key={`${assertion.relationship}-${index}`} assertion={assertion} />
            ))}
          </ul>
        </section>
      ))}

      <div className="reconciliation-actions">
        <div className="eyebrow">Expected topology</div>
        {editing ? (
          <div className="intent-form">
            <label>
              <span>What should be here?</span>
              <input
                className="input"
                maxLength={64}
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="e.g. Edge gateway"
              />
            </label>
            <label>
              <span>Kind of device</span>
              <select
                className="input"
                value={deviceType}
                onChange={(event) => setDeviceType(event.target.value as DeviceType)}
              >
                {DEVICE_TYPE_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option.replace("-", " ")}
                  </option>
                ))}
              </select>
            </label>
            <p className="intent-form__note">
              This is recorded in SwitchOps only. It is marked as expected, never as observed,
              and nothing is sent to the switch.
            </p>
            <div className="intent-form__actions">
              <button className="btn btn--ghost" disabled={busy} onClick={() => setEditing(false)}>
                Cancel
              </button>
              <button
                className="btn btn--primary"
                disabled={busy || !name.trim()}
                onClick={() =>
                  act(() =>
                    api.setTopologyIntent(deviceId, result.interface, {
                      expectedName: name.trim(),
                      expectedDeviceType: deviceType,
                    }),
                  )
                }
              >
                {busy ? "Saving…" : "Save expectation"}
              </button>
            </div>
          </div>
        ) : (
          <div className="reconciliation-actions__row">
            {observedName && result.status === "drift" ? (
              <button
                className="btn btn--primary"
                disabled={busy}
                onClick={() =>
                  act(() =>
                    api.setTopologyIntent(deviceId, result.interface, {
                      expectedName: observedName,
                      expectedDeviceType: result.observed?.deviceType || "unknown",
                      expectedVendor: result.observed?.vendor || null,
                      expectedModel: result.observed?.model || null,
                    }),
                  )
                }
              >
                Expect {observedName}
              </button>
            ) : null}
            <button className="btn" disabled={busy} onClick={() => setEditing(true)}>
              {intent ? "Edit expectation" : "Record expectation"}
            </button>
            <button
              className="btn"
              disabled={busy}
              onClick={() =>
                act(() =>
                  api.setTopologyIntent(deviceId, result.interface, {
                    expectedName: intent?.expectedName || result.expected?.objectLabel || result.interface,
                    expectedDeviceType: intent?.expectedDeviceType || "unknown",
                    suppressed: !intent?.suppressed,
                  }),
                )
              }
            >
              {intent?.suppressed ? "Unmute" : "Mute this interface"}
            </button>
            {intent ? (
              <button
                className="btn btn--ghost"
                disabled={busy}
                onClick={() => act(() => api.clearTopologyIntent(deviceId, result.interface))}
              >
                Clear
              </button>
            ) : null}
          </div>
        )}
        {error ? (
          <p className="settings-alert settings-alert--bad" role="alert">{error}</p>
        ) : null}
      </div>
    </div>
  );
}

function AssertionRow({ assertion }: { assertion: TopologyAssertion }) {
  return (
    <li className="assertion">
      <div className="assertion__head">
        <strong>{assertion.objectLabel}</strong>
        <span className="assertion__confidence">{assertion.confidence}</span>
      </div>
      <p className="assertion__detail">{assertion.detail}</p>
      <span className="assertion__source">from {SOURCE_COPY[assertion.source]}</span>
    </li>
  );
}
