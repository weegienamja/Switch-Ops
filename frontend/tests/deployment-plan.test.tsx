import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DeploymentPlanPanel from "@/components/DeploymentPlanPanel";
import { api } from "@/lib/api";
import type { InterfaceStatus } from "@/lib/types";

vi.mock("@/lib/api", () => ({ api: { planAccessPoint: vi.fn() } }));

const interfaces: InterfaceStatus[] = [
  {
    port: "Gi0/4",
    name: "TEST-AP-01 AP",
    status: "notconnect",
    vlan: "1",
    duplex: "auto",
    speed: "auto",
    type: "10/100/1000BaseTX",
    protected: false,
    policyState: "OPERABLE",
  },
  {
    port: "Gi0/1",
    name: "Uplink to Test Gateway",
    status: "connected",
    vlan: "1",
    duplex: "a-full",
    speed: "a-1000",
    type: "10/100/1000BaseTX",
    protected: true,
    policyState: "PROTECTED",
  },
];

function validPlan() {
  return {
    planId: "ap-123abc",
    status: "VALID" as const,
    targetInterface: "GigabitEthernet0/4",
    desiredState: {
      role: "wireless-access-point",
      enabled: true,
      mode: "access",
      vlan: 1,
      poe: "auto",
      portfast: true,
    },
    checks: [
      { name: "interface_exists", passed: true, detail: "Gi0/4 was returned by show interfaces status." },
      { name: "target_is_safe", passed: true, detail: "GigabitEthernet0/4 is OPERABLE in local policy." },
      { name: "poe_supported", passed: true, detail: "Gi0/4 is present in show power inline." },
      { name: "vlan_exists", passed: true, detail: "VLAN 1 exists in show vlan brief." },
    ],
    impact: "Selected access port only. Protected interfaces are unaffected.",
    proposedIos: ["configure terminal", "interface GigabitEthernet0/4", "switchport access vlan 1"],
    backupRequired: true,
    verificationCommands: ["show interfaces status", "show power inline"],
    applyAvailable: false as const,
  };
}

describe("DeploymentPlanPanel", () => {
  beforeEach(() => vi.clearAllMocks());

  it("reads as a deployment plan with named stages", async () => {
    vi.mocked(api.planAccessPoint).mockResolvedValue(validPlan());
    render(<DeploymentPlanPanel interfaces={interfaces} />);
    fireEvent.click(screen.getByRole("button", { name: /generate dry-run plan/i }));

    await waitFor(() => expect(screen.getByText(/Prepare Gi0\/4 for an access point/)).toBeTruthy());
    for (const stage of ["Plan", "Precheck", "Impact", "Desired state", "Proposed IOS", "Verification", "Backup"]) {
      expect(screen.getByText(stage)).toBeTruthy();
    }
    expect(screen.getByText("ap-123abc")).toBeTruthy();
    expect(screen.getByText("PLAN IS VALID")).toBeTruthy();
  });

  it("shows each precheck with the evidence behind it", async () => {
    vi.mocked(api.planAccessPoint).mockResolvedValue(validPlan());
    render(<DeploymentPlanPanel interfaces={interfaces} />);
    fireEvent.click(screen.getByRole("button", { name: /generate dry-run plan/i }));

    await waitFor(() => expect(screen.getAllByText("PASS")).toHaveLength(4));
    expect(screen.getByText("interface exists")).toBeTruthy();
    expect(screen.getByText("vlan exists")).toBeTruthy();
    expect(screen.getByText(/VLAN 1 exists in show vlan brief/)).toBeTruthy();
  });

  it("states the desired state as fields rather than raw JSON", async () => {
    vi.mocked(api.planAccessPoint).mockResolvedValue(validPlan());
    render(<DeploymentPlanPanel interfaces={interfaces} />);
    fireEvent.click(screen.getByRole("button", { name: /generate dry-run plan/i }));

    const state = await waitFor(() => {
      const element = document.querySelector(".plan-state");
      if (!element) throw new Error("desired state block has not rendered");
      return element as HTMLElement;
    });
    expect(within(state).getByText("role")).toBeTruthy();
    expect(within(state).getByText("wireless-access-point")).toBeTruthy();
    expect(within(state).getByText("portfast")).toBeTruthy();
    // Booleans are rendered as words, not "true".
    expect(within(state).queryByText("true")).toBeNull();
    expect(within(state).getAllByText("enabled").length).toBeGreaterThan(0);
  });

  it("keeps planning visibly non-executable", async () => {
    vi.mocked(api.planAccessPoint).mockResolvedValue(validPlan());
    render(<DeploymentPlanPanel interfaces={interfaces} />);

    expect(screen.getByText("DRY RUN ONLY")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^apply$/i })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /generate dry-run plan/i }));

    await waitFor(() => expect(screen.getByText("PLAN IS VALID")).toBeTruthy());
    expect(
      screen.getByText(/No configuration will be sent to the device. There is no apply endpoint/),
    ).toBeTruthy();
    expect(screen.getByText(/never sent to the device by this build/)).toBeTruthy();
    // No control anywhere could execute the plan.
    for (const button of screen.getAllByRole("button")) {
      expect(button.textContent || "").not.toMatch(/^(apply|execute|push|deploy|commit)$/i);
    }
  });

  it("offers no protected interface as a plan target", () => {
    render(<DeploymentPlanPanel interfaces={interfaces} />);
    const options = screen.getAllByRole("option").map((option) => option.textContent || "");
    expect(options.some((option) => option.startsWith("Gi0/4"))).toBe(true);
    expect(options.some((option) => option.startsWith("Gi0/1"))).toBe(false);
  });

  it("surfaces a blocked plan without pretending it is safe", async () => {
    vi.mocked(api.planAccessPoint).mockResolvedValue({
      ...validPlan(),
      status: "INVALID",
      proposedIos: [],
      impact: "Plan is blocked because the target is not OPERABLE in local policy.",
      checks: [
        { name: "target_is_safe", passed: false, detail: "GigabitEthernet0/1 is protected and cannot be modified." },
      ],
    });
    render(<DeploymentPlanPanel interfaces={interfaces} />);
    fireEvent.click(screen.getByRole("button", { name: /generate dry-run plan/i }));

    await waitFor(() => expect(screen.getByText("PLAN IS BLOCKED")).toBeTruthy());
    expect(screen.getByText("BLOCK")).toBeTruthy();
    expect(screen.getByText(/is protected and cannot be modified/)).toBeTruthy();
    // A blocked plan proposes no configuration at all.
    expect(screen.queryByText("Proposed IOS")).toBeNull();
  });
});
