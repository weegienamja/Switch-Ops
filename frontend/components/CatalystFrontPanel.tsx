"use client";

import type {
  NetworkEvent,
  NetworkInterface,
  TelemetrySnapshotSummary,
  TopologyModel,
} from "@/lib/types";
import {
  explainInterfaceStatus,
  explainLink,
  explainPoe,
  explainVlan,
  interfaceDeltaFor,
  statusFromNetworkInterface,
} from "@/lib/explanations";

const PORTS = Array.from({ length: 10 }, (_, index) => `Gi0/${index + 1}`);

function portState(networkInterface: NetworkInterface | undefined): string {
  if (!networkInterface) return "unknown";
  if (networkInterface.adminState === "down") return "disabled";
  if (networkInterface.operState === "up") return "connected";
  return "notconnect";
}

export default function CatalystFrontPanel({
  topology,
  telemetry,
  events,
  selectedPort,
  onSelectPort,
}: {
  topology: TopologyModel;
  telemetry: TelemetrySnapshotSummary;
  events: NetworkEvent[];
  selectedPort: string;
  onSelectPort: (port: string) => void;
}) {
  const byPort = new Map(topology.interfaces.map((item) => [item.port, item]));
  const selected = byPort.get(selectedPort);
  const delta = interfaceDeltaFor(selectedPort, telemetry.interfaceDeltas);
  const link = topology.links.find((item) => item.fromInterface === selectedPort);
  const endpoint = link
    ? topology.devices.find((device) => device.id === link.toDeviceId)
    : undefined;
  const recentEvents = events.filter((event) => event.interface === selectedPort).slice(0, 3);
  const state = selected ? portState(selected) : "unknown";

  return (
    <section className="card front-panel-card" aria-labelledby="front-panel-title">
      <div className="card__head">
        <div>
          <h2 className="card__title" id="front-panel-title">Physical switch</h2>
          <div className="card__subtitle">
            Simplified WS-C3560CG-8PC-S front panel. Select a port to trace it through the lab.
          </div>
        </div>
        <span className="badge">10 × Gigabit Ethernet</span>
      </div>

      <div className="switch-chassis">
        <div className="switch-chassis__identity">
          <span className="switch-chassis__maker">SWITCHOPS LAB VIEW</span>
          <strong>3560-CG</strong>
          <span>compact PoE switch</span>
        </div>
        <div className="switch-chassis__ports" role="group" aria-label="Physical switch ports">
          <div className="switch-chassis__bank">
            <span className="switch-chassis__bank-label">PoE access</span>
            <div className="switch-chassis__bank-grid">
              {PORTS.slice(0, 8).map((port) => (
                <FrontPort
                  key={port}
                  port={port}
                  networkInterface={byPort.get(port)}
                  selected={selectedPort === port}
                  delta={interfaceDeltaFor(port, telemetry.interfaceDeltas)?.errorDelta}
                  onSelect={onSelectPort}
                />
              ))}
            </div>
          </div>
          <div className="switch-chassis__bank switch-chassis__bank--uplink">
            <span className="switch-chassis__bank-label">Uplink</span>
            <div className="switch-chassis__bank-grid">
              {PORTS.slice(8).map((port) => (
                <FrontPort
                  key={port}
                  port={port}
                  networkInterface={byPort.get(port)}
                  selected={selectedPort === port}
                  delta={interfaceDeltaFor(port, telemetry.interfaceDeltas)?.errorDelta}
                  onSelect={onSelectPort}
                />
              ))}
            </div>
          </div>
        </div>
        <div className="switch-chassis__legend" aria-label="Port state legend">
          <span><i className="port-led port-led--up" /> Link</span>
          <span><i className="port-led port-led--waiting" /> Waiting</span>
          <span><i className="port-led port-led--disabled" /> Disabled</span>
        </div>
      </div>

      <div className="port-inspector" aria-live="polite">
        <div className="port-inspector__primary">
          <div className="eyebrow">Selected interface</div>
          <div className="port-inspector__title-row">
            <h3>{selectedPort}</h3>
            <span className={`badge state-${state}`}>{state}</span>
            {selected?.protected ? <span className="badge badge--cyan">protected</span> : null}
          </div>
          <p className="port-inspector__description">
            {selected?.description || "No interface description is configured."}
          </p>
          <dl className="detail-grid">
            <div><dt>Speed</dt><dd>{selected?.speed || "—"}</dd></div>
            <div><dt>Duplex</dt><dd>{selected?.duplex || "—"}</dd></div>
            <div><dt>VLAN</dt><dd>{selected?.vlan || "—"}</dd></div>
            <div><dt>PoE</dt><dd>{selected?.poeState || "—"}{selected?.poeWatts ? ` · ${selected.poeWatts.toFixed(1)} W` : ""}</dd></div>
            <div><dt>Error change</dt><dd>{delta?.errorDelta == null ? "baseline" : `+${delta.errorDelta}`}</dd></div>
            <div><dt>Connected object</dt><dd>{endpoint?.name || "None observed"}</dd></div>
          </dl>
        </div>
        <div className="port-inspector__why">
          <div className="eyebrow">Why?</div>
          <p>{selected ? explainInterfaceStatus(statusFromNetworkInterface(selected).status) : "No telemetry is available for this port."}</p>
          {selected?.operState === "up" ? <p>{explainLink(selected.speed, selected.duplex)}</p> : null}
          {selected ? <p>{explainVlan(selected.vlan)}</p> : null}
          {selected?.poeCapable ? <p>{explainPoe(selected.poeState, selected.poeWatts)}</p> : null}
        </div>
        <div className="port-inspector__events">
          <div className="eyebrow">Recent events</div>
          {recentEvents.length ? (
            <ul className="compact-events">
              {recentEvents.map((event, index) => (
                <li key={event.id ?? `${event.timestamp}-${index}`}>
                  <span className={`event-mark event-mark--${event.severity.toLowerCase()}`} />
                  <span>{event.title}</span>
                  <time>{new Date(event.timestamp).toLocaleTimeString()}</time>
                </li>
              ))}
            </ul>
          ) : (
            <p className="empty-note">No recorded state change for this port yet.</p>
          )}
        </div>
      </div>
    </section>
  );
}

function FrontPort({
  port,
  networkInterface,
  selected,
  delta,
  onSelect,
}: {
  port: string;
  networkInterface?: NetworkInterface;
  selected: boolean;
  delta?: number | null;
  onSelect: (port: string) => void;
}) {
  const state = portState(networkInterface);
  const stateLabel = state === "connected" ? "LINK" : state === "disabled" ? "OFF" : state === "notconnect" ? "WAIT" : "N/A";
  return (
    <button
      type="button"
      className={`front-port front-port--${state} ${selected ? "front-port--selected" : ""}`}
      onClick={() => onSelect(port)}
      aria-pressed={selected}
      aria-label={`${port}, ${state}, ${networkInterface?.description || "no description"}`}
      title={`${port} · ${networkInterface?.description || state}`}
    >
      <span className="front-port__label">{port.replace("Gi0/", "")}</span>
      <span className="front-port__jack" aria-hidden>
        <span className="front-port__pins" />
      </span>
      <span className="front-port__state">{stateLabel}</span>
      {(delta || 0) > 0 ? <span className="front-port__alert">+{delta}</span> : null}
    </button>
  );
}
