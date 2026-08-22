"use client";

import type {
  NetworkEvent,
  TelemetrySnapshotSummary,
  TopologyModel,
} from "@/lib/types";
import CatalystFrontPanel from "./CatalystFrontPanel";
import LogicalTopology from "./LogicalTopology";

export default function NetworkTwin({
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
  return (
    <div className="network-twin">
      <CatalystFrontPanel
        topology={topology}
        telemetry={telemetry}
        events={events}
        selectedPort={selectedPort}
        onSelectPort={onSelectPort}
      />
      <LogicalTopology
        topology={topology}
        selectedPort={selectedPort}
        onSelectPort={onSelectPort}
      />
    </div>
  );
}
