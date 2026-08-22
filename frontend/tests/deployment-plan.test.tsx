import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DeploymentPlanPanel from "@/components/DeploymentPlanPanel";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { planAccessPoint: vi.fn() } }));

describe("DeploymentPlanPanel", () => {
  beforeEach(() => vi.clearAllMocks());

  it("keeps planning visibly non-executable", async () => {
    vi.mocked(api.planAccessPoint).mockResolvedValue({
      planId: "ap-123",
      status: "VALID",
      targetInterface: "GigabitEthernet0/4",
      desiredState: { vlan: 1 },
      checks: [{ name: "target_is_safe", passed: true, detail: "Safe lab port." }],
      impact: "Selected access port only.",
      proposedIos: ["interface GigabitEthernet0/4", "switchport access vlan 1"],
      backupRequired: true,
      verificationCommands: ["show interfaces status"],
      applyAvailable: false,
    });
    render(<DeploymentPlanPanel interfaces={[{
      port: "Gi0/4",
      name: "TEST-AP-01 AP",
      status: "notconnect",
      vlan: "1",
      duplex: "auto",
      speed: "auto",
      type: "10/100/1000BaseTX",
      protected: false,
    }]} />);

    expect(screen.getByText("DRY RUN ONLY")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^apply$/i })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /generate dry-run plan/i }));

    await waitFor(() => expect(screen.getByText("Plan is valid")).toBeTruthy());
    expect(screen.getByText(/APPLY UNAVAILABLE/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^apply$/i })).toBeNull();
  });
});
