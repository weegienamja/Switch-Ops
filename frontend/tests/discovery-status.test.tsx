import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import DiscoveryStatusPanel from "@/components/DiscoveryStatusPanel";
import type { DiscoveryStatus } from "@/lib/types";


const discovery: DiscoveryStatus = {
  lldp: {
    state: "disabled",
    supported: true,
    enabled: false,
    neighbors: [],
    detail: "LLDP is supported but disabled; no LLDP identity evidence is being claimed.",
  },
  localEndpoint: {
    state: "confirmed",
    interface: "Gi0/2",
    label: "This SwitchOps PC",
    ip: "192.0.2.95",
    detail: "One active local adapter matched the only learned address on this connected access port.",
  },
  snmp: {
    configured: true,
    versions: ["v1/v2c"],
    readOnlyCommunities: 1,
    readWriteCommunities: 0,
    v3Users: 0,
    trapHosts: 0,
    detail: "Existing SNMP configuration was detected read-only; SwitchOps did not use or change it.",
  },
};


describe("progressive discovery status", () => {
  it("keeps disabled LLDP, confirmed local identity, and SNMP capability separate", () => {
    render(<DiscoveryStatusPanel discovery={discovery} />);
    expect(screen.getByText("DISABLED")).toBeTruthy();
    expect(screen.getByText("This SwitchOps PC · Gi0/2")).toBeTruthy();
    expect(screen.getByText("EXISTING · v1/v2c")).toBeTruthy();
    expect(screen.getByText(/no LLDP identity evidence/)).toBeTruthy();
  });

  it("renders counts only and never needs credential identifiers", () => {
    const { container } = render(<DiscoveryStatusPanel discovery={discovery} />);
    expect(container.textContent).toContain("RO communities 1 · RW communities 0 · v3 users 0");
    expect(container.textContent).not.toContain("community name");
  });
});
