"use client";

import type {
  ExpectedRelationship,
  NetworkEvent,
  ReconciliationSummary,
  TelemetrySnapshotSummary,
  TopologyModel,
} from "@/lib/types";
import ReconciliationInspector from "./ReconciliationInspector";
import { explainPort, interfaceDeltaFor } from "@/lib/explanations";
import {
  EVIDENCE_COPY,
  IDENTITY_COPY,
  deviceStateLabel,
  learnedBehindNote,
} from "@/lib/evidence";
import { portState } from "./CatalystFrontPanel";
import DeviceArt from "./DeviceArt";

const STATE_WORD: Record<string, string> = {
  connected: "connected",
  notconnect: "notconnect",
  disabled: "disabled",
  unknown: "unknown",
};

/**
 * Everything known about one interface: the facts, why the switch reports
 * them, what is on the other end, and how confident that claim is.
 */
export default function PortInspector({
  topology,
  telemetry,
  events,
  selectedPort,
  reconciliation,
  intent,
  onIntentChange,
}: {
  topology: TopologyModel;
  telemetry: TelemetrySnapshotSummary;
  events: NetworkEvent[];
  selectedPort: string;
  reconciliation?: ReconciliationSummary;
  intent?: ExpectedRelationship[];
  onIntentChange?: () => void;
}) {
  const selected = topology.interfaces.find((item) => item.port === selectedPort);
  const delta = interfaceDeltaFor(selectedPort, telemetry.interfaceDeltas);
  const link = topology.links.find((item) => item.fromInterface === selectedPort);
  const endpoint = link
    ? topology.devices.find((device) => device.id === link.toDeviceId)
    : undefined;
  const recentEvents = events.filter((event) => event.interface === selectedPort).slice(0, 4);
  const state = selected ? portState(selected) : "unknown";
  const facts = explainPort(selected, delta);
  const reconciled = reconciliation?.interfaces.find(
    (item) => item.interface === selectedPort,
  );
  const portIntent = (intent || []).find((item) => item.interface === selectedPort);
  const behindNote = learnedBehindNote(link?.learnedMacCount || 0);

  return (
    <section className="card port-inspector-card" aria-labelledby="port-inspector-title" aria-live="polite">
      <div className="card__head">
        <div>
          <h2 className="card__title" id="port-inspector-title">
            {selectedPort}
          </h2>
          <div className="card__subtitle">
            {selected?.description?.trim() || "No interface description is configured."}
          </div>
        </div>
        <div className="port-inspector__badges">
          <span className={`badge state-${STATE_WORD[state]}`}>{state}</span>
          {selected?.protected ? <span className="badge badge--cyan">protected</span> : null}
          {selected?.role === "uplink" ? <span className="badge">uplink</span> : null}
        </div>
      </div>

      <div className="port-inspector">
        <div className="port-inspector__column">
          <div className="eyebrow">Reported values</div>
          <dl className="detail-grid">
            <div><dt>Speed</dt><dd>{selected?.speed || "—"}</dd></div>
            <div><dt>Duplex</dt><dd>{selected?.duplex || "—"}</dd></div>
            <div><dt>VLAN</dt><dd>{selected?.vlan || "—"}</dd></div>
            <div>
              <dt>PoE</dt>
              <dd>
                {selected?.poeState || "—"}
                {selected?.poeWatts ? ` · ${selected.poeWatts.toFixed(1)} W` : ""}
              </dd>
            </div>
            <div>
              <dt>Error change</dt>
              <dd>{delta?.errorDelta == null ? "baseline" : `+${delta.errorDelta}`}</dd>
            </div>
            <div>
              <dt>Addresses learned</dt>
              <dd>{selected?.learnedMacCount ?? 0}</dd>
            </div>
          </dl>

          <div className="eyebrow port-inspector__section">On the other end</div>
          {endpoint ? (
            <div className="port-endpoint">
              <DeviceArt type={endpoint.visualCategory} label={endpoint.name} width={60} />
              <div className="port-endpoint__copy">
                <strong>{endpoint.name}</strong>
                <span>{deviceStateLabel(endpoint)}</span>
                <span className={`evidence-tag evidence-tag--${endpoint.evidenceLevel}`}>
                  {EVIDENCE_COPY[endpoint.evidenceLevel].label}
                </span>
              </div>
            </div>
          ) : (
            <p className="empty-note">
              Nothing is evidenced on this interface.
            </p>
          )}
          {endpoint ? (
            <>
              <p className="port-inspector__evidence">
                {EVIDENCE_COPY[endpoint.evidenceLevel].detail}
              </p>
              <p className="port-inspector__evidence port-inspector__evidence--identity">
                {IDENTITY_COPY[endpoint.identitySource]}
              </p>
              {behindNote ? <p className="port-inspector__evidence">{behindNote}</p> : null}
            </>
          ) : null}
        </div>

        <div className="port-inspector__column">
          <div className="eyebrow">Why is it like this?</div>
          <ul className="fact-list">
            {facts.map((fact) => (
              <li key={fact.key} className="fact">
                <span className="fact__title">{fact.title}</span>
                <p className="fact__detail">{fact.detail}</p>
                {fact.learnMore ? (
                  <details className="fact__more">
                    <summary>Learn more</summary>
                    <p>{fact.learnMore}</p>
                  </details>
                ) : null}
              </li>
            ))}
          </ul>
        </div>

        <div className="port-inspector__column port-inspector__column--reconciliation">
          <div className="eyebrow">Expected vs observed</div>
          {reconciliation ? (
            <ReconciliationInspector
              deviceId={reconciliation.deviceId}
              result={reconciled}
              intent={portIntent}
              onIntentChange={onIntentChange || (() => undefined)}
            />
          ) : (
            <p className="empty-note">Reconciliation was unavailable for this observation.</p>
          )}
        </div>

        <div className="port-inspector__column port-inspector__column--events">
          <div className="eyebrow">Recorded changes</div>
          {recentEvents.length ? (
            <ul className="compact-events">
              {recentEvents.map((event, index) => (
                <li key={event.id ?? `${event.timestamp}-${index}`}>
                  <span className={`event-mark event-mark--${event.severity.toLowerCase()}`} aria-hidden />
                  <span>{event.title}</span>
                  <time dateTime={event.timestamp}>
                    {new Date(event.timestamp).toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </time>
                </li>
              ))}
            </ul>
          ) : (
            <p className="empty-note">
              No state change has been recorded for this port between observations yet.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
