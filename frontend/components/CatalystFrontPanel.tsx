"use client";

import type { NetworkInterface, TelemetrySnapshotSummary, TopologyModel } from "@/lib/types";
import { interfaceDeltaFor } from "@/lib/explanations";

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

function interfaceOrder(left: NetworkInterface, right: NetworkInterface): number {
  return left.port.localeCompare(right.port, undefined, { numeric: true, sensitivity: "base" });
}

/**
 * A model-neutral Catalyst front panel based on interfaces actually observed.
 * Ports carry `data-port` so the topology canvas can draw an evidence link to
 * the physical interface reported by the device.
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
  const ports = topology.interfaces
    .filter((item) => /^(Fa|Gi|Te|Twe|Fo|Hu)/.test(item.port))
    .sort(interfaceOrder);

  return (
    <div className="switch-chassis">
      <div className="switch-chassis__identity">
        <span className="switch-chassis__maker">CISCO CATALYST</span>
        <strong>{model || "IOS switch"}</strong>
        <span>{ports.length} observed physical interfaces</span>
      </div>
      <div className="switch-chassis__ports" role="group" aria-label="Physical switch ports">
        <div className="switch-chassis__bank">
          <span className="switch-chassis__bank-label">Observed physical interfaces</span>
          <div className="switch-chassis__bank-grid">
            {ports.map((item) => (
              <FrontPort
                key={item.port}
                port={item.port}
                networkInterface={byPort.get(item.port)}
                selected={selectedPort === item.port}
                delta={interfaceDeltaFor(item.port, telemetry.interfaceDeltas)?.errorDelta}
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
        <span><i className="port-led port-led--protected" aria-hidden /> protected by local policy</span>
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
    networkInterface ? `${(networkInterface.policyState || "UNMANAGED").toLowerCase()} write policy` : null,
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
        {port}
        {networkInterface?.policyState === "PROTECTED" ? (
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
