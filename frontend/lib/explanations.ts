import type { InterfaceDelta, InterfaceStatus, NetworkInterface } from "./types";

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
