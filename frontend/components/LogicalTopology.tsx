"use client";

import type { TopologyModel } from "@/lib/types";
import DeviceVisual from "./DeviceVisual";

export default function LogicalTopology({
  topology,
  selectedPort,
  onSelectPort,
}: {
  topology: TopologyModel;
  selectedPort: string;
  onSelectPort: (port: string) => void;
}) {
  const root = topology.devices.find((device) => device.id === topology.rootDeviceId);
  const endpoints = topology.devices.filter((device) => device.id !== topology.rootDeviceId);

  return (
    <section className="card logical-topology" aria-labelledby="logical-topology-title">
      <div className="card__head">
        <div>
          <h2 className="card__title" id="logical-topology-title">Evidence map</h2>
          <div className="card__subtitle">
            Solid links are observed. Dashed links are expectations inferred from interface descriptions.
          </div>
        </div>
        <span className="badge">{endpoints.length} visual objects</span>
      </div>

      <div className="topology-canvas">
        <div className="topology-root">
          <DeviceVisual type="switch" label={root?.name || "Switch"} size={92} />
          <div>
            <strong>{root?.name || "Managed switch"}</strong>
            <span>{root?.model || "Model unknown"}</span>
            <span className="evidence-tag evidence-tag--observed">Observed · high confidence</span>
          </div>
        </div>

        <div className="topology-endpoints">
          {endpoints.map((device) => {
            const link = topology.links.find((candidate) => candidate.toDeviceId === device.id);
            const selected = link?.fromInterface === selectedPort;
            return (
              <button
                type="button"
                key={device.id}
                className={`topology-node topology-node--${link?.status || "unknown"} ${selected ? "topology-node--selected" : ""}`}
                onClick={() => link?.fromInterface && onSelectPort(link.fromInterface)}
                aria-pressed={selected}
              >
                <span className={`topology-link topology-link--${link?.status || "unknown"}`} aria-hidden />
                <span className="topology-link-label">
                  {link?.fromInterface || "unknown port"}
                  {link?.speed && link.speed !== "auto" ? ` · ${link.speed.replace("a-", "")}` : ""}
                  {link?.poe ? " · PoE" : ""}
                </span>
                <DeviceVisual
                  type={device.visualCategory}
                  label={device.name}
                  expected={device.source === "expected"}
                />
                <span className="topology-node__copy">
                  <strong>{device.name}</strong>
                  <span>{device.model || device.vendor || device.type.replace("-", " ")}</span>
                  <span className={`evidence-tag evidence-tag--${device.source}`}>
                    {device.source === "expected" ? "Waiting · description evidence" : `${device.source} · ${device.confidence} confidence`}
                  </span>
                </span>
                <span className="topology-node__state">
                  {device.online ? "ONLINE" : device.source === "expected" ? "EXPECTED" : "OFFLINE"}
                </span>
              </button>
            );
          })}
          {endpoints.length === 0 ? (
            <div className="topology-empty">
              <DeviceVisual type="unknown" label="Unknown device" expected />
              <span>No endpoints are currently evidenced by interface descriptions or learned MACs.</span>
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}
