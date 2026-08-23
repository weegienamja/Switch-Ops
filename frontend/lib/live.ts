import type {
  InterfaceStatus,
  LiveInterfaceState,
  NetworkInterface,
  PoeResponse,
  TopologyModel,
} from "./types";
import type { InterfaceStatusResponse } from "./api";

export interface LiveMergedData {
  topology: TopologyModel;
  interfaces: InterfaceStatusResponse;
  poe: PoeResponse;
}

/** Overlay ephemeral live fields without rewriting deep-observation evidence. */
export function mergeLiveInterfaces(
  topology: TopologyModel,
  interfaces: InterfaceStatusResponse,
  poe: PoeResponse,
  liveInterfaces: LiveInterfaceState[],
): LiveMergedData {
  if (!liveInterfaces.length) return { topology, interfaces, poe };
  const liveByPort = new Map(liveInterfaces.map((item) => [item.port, item]));

  const mergeNetworkInterface = (item: NetworkInterface): NetworkInterface => {
    const live = liveByPort.get(item.port);
    if (!live) return item;
    return {
      ...item,
      description: live.description,
      adminState: live.admin_state,
      operState: live.oper_state,
      speed: live.speed,
      duplex: live.duplex,
      vlan: live.vlan,
      poeState: live.poe_state || item.poeState,
      poeWatts: live.poe_watts,
      protected: live.protected,
      policyState: live.policy_state,
    };
  };

  const mergeStatus = (item: InterfaceStatus): InterfaceStatus => {
    const live = liveByPort.get(item.port);
    return live
      ? {
          ...item,
          name: live.description,
          status: live.status,
          vlan: live.vlan,
          duplex: live.duplex,
          speed: live.speed,
          protected: live.protected,
          policyState: live.policy_state,
        }
      : item;
  };

  const topologyInterfaces = topology.interfaces.map(mergeNetworkInterface);
  const stateByPort = new Map(topologyInterfaces.map((item) => [item.port, item]));
  return {
    topology: {
      ...topology,
      interfaces: topologyInterfaces,
      links: topology.links.map((link) => {
        const item = stateByPort.get(link.fromInterface);
        if (!item) return link;
        const status =
          item.operState === "up" ? "up" : item.adminState === "down" ? "down" : "waiting";
        return { ...link, status };
      }),
    },
    interfaces: { interfaces: interfaces.interfaces.map(mergeStatus) },
    poe: {
      ...poe,
      ports: poe.ports.map((item) => {
        const live = liveByPort.get(item.interface);
        return live
          ? { ...item, oper: live.poe_state || item.oper, powerWatts: live.poe_watts }
          : item;
      }),
    },
  };
}
