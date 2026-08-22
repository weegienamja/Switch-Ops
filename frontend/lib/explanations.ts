import type { InterfaceDelta, InterfaceStatus, NetworkInterface } from "./types";

/**
 * Deterministic explanations of observed switch state.
 *
 * Every string here is derived from something the switch actually reported.
 * Nothing is predicted, and nothing claims a cause that the data does not
 * show. Keep the headline short enough to scan and put depth behind
 * `learnMore` so a beginner is never handed a wall of text.
 */

export interface PortFact {
  key: string;
  /** Scannable state word, e.g. "1 GBPS". */
  title: string;
  /** One sentence of plain English. */
  detail: string;
  /** Optional background, revealed on demand. */
  learnMore?: string;
}

export function explainInterfaceStatus(status: string): string {
  switch (status.toLowerCase()) {
    case "disabled":
      return "This interface has been administratively shut down. It will not establish a link until it is enabled.";
    case "notconnect":
      return "This interface is enabled, but the switch does not currently detect an Ethernet link.";
    case "connected":
      return "The switch detects an active Ethernet link on this interface.";
    case "err-disabled":
      return "IOS has disabled this interface after detecting a condition that requires investigation.";
    default:
      return "The switch reported this state, but SwitchOps does not have a more specific deterministic explanation.";
  }
}

export function explainLink(speed: string, duplex: string): string {
  const normalizedSpeed = speed.toLowerCase().replace("a-", "");
  const normalizedDuplex = duplex.toLowerCase().replace("a-", "");
  const speedText = normalizedSpeed === "1000"
    ? "1 gigabit per second"
    : normalizedSpeed === "100"
      ? "100 megabits per second"
      : normalizedSpeed === "10"
        ? "10 megabits per second"
        : "the reported speed";
  const duplexText = normalizedDuplex === "full"
    ? "both sides can transmit and receive simultaneously"
    : normalizedDuplex === "half"
      ? "only one side transmits at a time"
      : "duplex could not be determined";
  return `The link is operating at ${speedText}; ${duplexText}.`;
}

export function explainVlan(vlan: string): string {
  if (!vlan) return "The switch did not report a VLAN for this interface.";
  if (vlan.toLowerCase() === "trunk") {
    return "This interface can carry traffic for multiple VLANs.";
  }
  return `This interface currently belongs to VLAN ${vlan}. A VLAN is a separate logical Ethernet network.`;
}

export function explainPoe(poeState: string, watts = 0): string {
  if (["", "off", "n/a", "not-supported"].includes(poeState.toLowerCase())) {
    return "The switch is not currently supplying power through this Ethernet port.";
  }
  return `The switch is supplying Power over Ethernet on this port${watts > 0 ? ` (${watts.toFixed(1)} W)` : ""}.`;
}

function speedTitle(speed: string): string {
  const value = speed.toLowerCase().replace("a-", "");
  if (value === "1000") return "1 GBPS";
  if (value === "100") return "100 MBPS";
  if (value === "10") return "10 MBPS";
  return "SPEED REPORTED";
}

function duplexTitle(duplex: string): string {
  const value = duplex.toLowerCase().replace("a-", "");
  if (value === "full") return "FULL DUPLEX";
  if (value === "half") return "HALF DUPLEX";
  return "DUPLEX UNKNOWN";
}

/**
 * Build the ordered list of facts shown under "Why?" for one interface.
 * Only facts the switch actually reported are included.
 */
export function explainPort(
  networkInterface: NetworkInterface | undefined,
  delta?: InterfaceDelta,
): PortFact[] {
  if (!networkInterface) {
    return [{
      key: "unknown",
      title: "NO TELEMETRY",
      detail: "The last observation did not include a status row for this interface.",
    }];
  }

  const facts: PortFact[] = [];
  const status = statusFromNetworkInterface(networkInterface).status;

  if (status === "connected") {
    facts.push({
      key: "status",
      title: "CONNECTED",
      detail: "The switch detects an active Ethernet link on this interface.",
      learnMore:
        "A link means the two Ethernet transceivers can see each other electrically. It does not prove that traffic is flowing or that the far end is configured correctly.",
    });
  } else if (status === "disabled") {
    facts.push({
      key: "status",
      title: "DISABLED",
      detail:
        "This port has been administratively shut down and will not establish a link until it is enabled.",
      learnMore:
        "An administrator ran \"shutdown\" on the interface. The port stays dark even with a cable plugged in. This is a configuration state, not a fault.",
    });
  } else {
    facts.push({
      key: "status",
      title: "NOTCONNECT",
      detail:
        "This port is enabled, but the switch does not currently detect an Ethernet link.",
      learnMore:
        "Common causes are nothing plugged in, a powered-off device, or a cable fault. The switch cannot tell these apart, so SwitchOps does not guess between them.",
    });
  }

  if (networkInterface.operState === "up") {
    facts.push({
      key: "speed",
      title: speedTitle(networkInterface.speed),
      detail: `The connection negotiated at ${speedTitle(networkInterface.speed).toLowerCase().replace("gbps", "gigabit per second").replace("mbps", "megabits per second")}.`,
      learnMore:
        "Speed is agreed by auto-negotiation between the switch port and the device. A lower speed than expected usually points at the cable or the far-end adapter.",
    });
    facts.push({
      key: "duplex",
      title: duplexTitle(networkInterface.duplex),
      detail:
        networkInterface.duplex.toLowerCase().replace("a-", "") === "full"
          ? "Both sides can transmit and receive at the same time."
          : networkInterface.duplex.toLowerCase().replace("a-", "") === "half"
            ? "Only one side transmits at a time, so collisions are possible."
            : "The switch did not report a duplex value for this interface.",
    });
  }

  if (networkInterface.vlan) {
    facts.push({
      key: "vlan",
      title: networkInterface.vlan.toLowerCase() === "trunk" ? "TRUNK" : `VLAN ${networkInterface.vlan}`,
      detail: explainVlan(networkInterface.vlan),
      learnMore:
        "A VLAN is a separate logical Ethernet network sharing the same physical switch. Devices in different VLANs need a router to talk to each other.",
    });
  }

  if (networkInterface.poeCapable) {
    const inactive = ["", "off", "n/a", "not-supported"].includes(
      networkInterface.poeState.toLowerCase(),
    );
    facts.push({
      key: "poe",
      title: inactive ? "POE OFF" : `POE ${networkInterface.poeState.toUpperCase()}`,
      detail: explainPoe(networkInterface.poeState, networkInterface.poeWatts),
      learnMore:
        "Power over Ethernet delivers power down the same cable as data. The switch only energises a port once it detects a device that asks for power, so \"off\" on an empty port is normal.",
    });
  }

  if (networkInterface.role === "uplink") {
    facts.push({
      key: "role",
      title: "UPLINK",
      detail:
        "This interface is treated as facing upstream, so addresses learned here usually belong to devices further away.",
      learnMore:
        "SwitchOps infers the uplink role from the interface description or a trunk VLAN. It changes how evidence is read: many addresses behind one uplink port is normal and does not mean many devices are plugged into it.",
    });
  }

  if (networkInterface.protected) {
    facts.push({
      key: "protected",
      title: "PROTECTED",
      detail:
        "SwitchOps refuses every configuration change on this interface, so a mistake here cannot cut off management access.",
    });
  }

  if (delta && delta.counterState !== "first") {
    const errorDelta = delta.errorDelta || 0;
    facts.push({
      key: "errors",
      title: errorDelta > 0 ? `+${errorDelta} ERRORS` : "NO NEW ERRORS",
      detail:
        errorDelta > 0
          ? `This interface recorded ${errorDelta} additional error(s) since the previous observation.`
          : "Interface error counters have not moved since the previous observation.",
      learnMore:
        "Cisco error counters are cumulative since the last reset, so an old non-zero total is history. Only a change between two observations is evidence of a current problem.",
    });
  }

  return facts;
}

export function interfaceDeltaFor(
  port: string,
  deltas: InterfaceDelta[],
): InterfaceDelta | undefined {
  return deltas.find((delta) => delta.port === port);
}

export function statusFromNetworkInterface(networkInterface: NetworkInterface): InterfaceStatus {
  return {
    port: networkInterface.port,
    name: networkInterface.description,
    status: networkInterface.adminState === "down"
      ? "disabled"
      : networkInterface.operState === "up"
        ? "connected"
        : "notconnect",
    vlan: networkInterface.vlan,
    duplex: networkInterface.duplex,
    speed: networkInterface.speed,
    type: "",
    protected: networkInterface.protected,
  };
}
