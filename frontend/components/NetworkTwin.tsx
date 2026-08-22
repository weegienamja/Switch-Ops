"use client";

import type {
  NetworkEvent,
  TelemetrySnapshotSummary,
  TopologyModel,
} from "@/lib/types";
import NetworkMap from "./NetworkMap";
import PortInspector from "./PortInspector";

/**
 * The Visual network view: the lab drawn as one picture, then everything
 * known about whichever port is selected.
 */
export default function NetworkTwin({
  topology,
  telemetry,
  events,
  selectedPort,
  onSelectPort,
  model,
}: {
  topology: TopologyModel;
  telemetry: TelemetrySnapshotSummary;
  events: NetworkEvent[];
  selectedPort: string;
  onSelectPort: (port: string) => void;
  model?: string;
}) {
  return (
    <div className="network-twin">
      <NetworkMap
        topology={topology}
        telemetry={telemetry}
        selectedPort={selectedPort}
        onSelectPort={onSelectPort}
        model={model}
      />
      <PortInspector
        topology={topology}
        telemetry={telemetry}
        events={events}
        selectedPort={selectedPort}
      />
    </div>
  );
}
