"use client";

import type { NetworkInterface, TelemetrySnapshotSummary, TopologyModel } from "@/lib/types";
import { interfaceDeltaFor } from "@/lib/explanations";

const PORTS = Array.from({ length: 10 }, (_, index) => `Gi0/${index + 1}`);

export type PortState = "connected" | "notconnect" | "disabled" | "unknown";

export function portState(networkInterface: NetworkInterface | undefined): PortState {
  if (!networkInterface) return "unknown";
  if (networkInterface.adminState === "down") return "disabled";
  if (networkInterface.operState === "up") return "connected";
  return "notconnect";
}

const STATE_LABEL: Record<PortState, string> = {
  connected: "LINK",
  notconnect: "WAIT",
  disabled: "OFF",
  unknown: "N/A",
};

/** Long form used for screen readers and tooltips — never colour alone. */
const STATE_DESCRIPTION: Record<PortState, string> = {
  connected: "link established",
  notconnect: "enabled, no link detected",
  disabled: "administratively disabled",
  unknown: "no status reported",
};

function shortSpeed(speed: string): string | null {
  const value = speed.toLowerCase().replace("a-", "");
  if (value === "1000") return "1G";
  if (value === "100") return "100M";
  if (value === "10") return "10M";
  return null;
}

/**
 * Simplified WS-C3560CG-8PC-S front panel: 8 PoE access ports plus the
 * 2 uplink-style interfaces. Ports carry `data-port` so the topology canvas
 * can draw a wire from a device to the physical port it is plugged into.
 */
export default function CatalystFrontPanel({
  topology,
  telemetry,
  selectedPort,
  onSelectPort,
  model,
}: {
  topology: TopologyModel;
  telemetry: TelemetrySnapshotSummary;
  selectedPort: string;
  onSelectPort: (port: string) => void;
  model?: string;
}) {
  const byPort = new Map(topology.interfaces.map((item) => [item.port, item]));

  return (
    <div className="switch-chassis">
      <div className="switch-chassis__identity">
        <span className="switch-chassis__maker">CISCO CATALYST</span>
        <strong>{model || "3560-CG"}</strong>
        <span>compact PoE switch</span>
      </div>
      <div className="switch-chassis__ports" role="group" aria-label="Physical switch ports">
        <div className="switch-chassis__bank">
          <span className="switch-chassis__bank-label">PoE access · 1–8</span>
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
          <span className="switch-chassis__bank-label">Uplink · 9–10</span>
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
        <span><i className="port-led port-led--up" aria-hidden /> LINK — connected</span>
        <span><i className="port-led port-led--waiting" aria-hidden /> WAIT — no link</span>
        <span><i className="port-led port-led--disabled" aria-hidden /> OFF — disabled</span>
      </div>
    </div>
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
  const speed = state === "connected" && networkInterface ? shortSpeed(networkInterface.speed) : null;
  const poeActive = Boolean(
    networkInterface?.poeCapable &&
      !["", "off", "n/a", "not-supported"].includes(networkInterface.poeState.toLowerCase()),
  );
  const errorDelta = delta || 0;
  const description = networkInterface?.description?.trim();

  const srDetail = [
    STATE_DESCRIPTION[state],
    description ? `described as ${description}` : "no description",
    speed ? `${speed === "1G" ? "1 gigabit" : speed} link` : null,
    poeActive ? "supplying Power over Ethernet" : null,
    networkInterface?.protected ? "protected from configuration changes" : null,
    errorDelta > 0 ? `${errorDelta} new errors since the previous observation` : null,
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <button
      type="button"
      data-port={port}
      className={`front-port front-port--${state} ${selected ? "front-port--selected" : ""}`}
      onClick={() => onSelect(port)}
      aria-pressed={selected}
      aria-label={`${port}, ${srDetail}`}
      title={`${port} · ${description || STATE_DESCRIPTION[state]}`}
    >
      <span className="front-port__label">
        {port.replace("Gi0/", "")}
        {networkInterface?.protected ? (
          <i className="front-port__protected" aria-hidden title="protected">
            ▪
          </i>
        ) : null}
      </span>
      <span className="front-port__jack" aria-hidden>
        <span className="front-port__pins" />
      </span>
      <span className="front-port__state">{STATE_LABEL[state]}</span>
      <span className="front-port__meta" aria-hidden>
        {poeActive ? <i className="front-port__poe" title="PoE delivering">P</i> : null}
        {speed ? <i className="front-port__speed">{speed}</i> : null}
      </span>
      {errorDelta > 0 ? (
        <span className="front-port__alert" aria-hidden title={`${errorDelta} new errors`}>
          +{errorDelta}
        </span>
      ) : null}
    </button>
  );
}
