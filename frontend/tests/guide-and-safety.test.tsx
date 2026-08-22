import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import LabGuide from "@/components/LabGuide";
import SafeControlPanel from "@/components/SafeControlPanel";
import type { GuideOperation, InterfaceStatus, SetupStatus } from "@/lib/types";

const interfaceStatus: InterfaceStatus = {
  port: "Gi0/4",
  name: "Lab access point",
  status: "notconnect",
  vlan: "1",
  duplex: "auto",
  speed: "auto",
  type: "10/100/1000BaseTX",
  protected: false,
};

const operation: GuideOperation = {
  id: "connected_ports",
  category: "GETTING STARTED",
  title: "Check connected ports",
  question: "Which ports are connected?",
  whatItTellsYou: "Explains which physical interfaces have a link.",
  safety: "READ ONLY",
  commands: ["show interfaces status"],
  requiresInterface: false,
};

describe("Lab Guide and write safety", () => {
  it("renders an allowlisted operation, exact command, and read-only boundary", () => {
    render(<LabGuide operations={[operation]} interfaces={[interfaceStatus]} />);
    expect(screen.getAllByText("READ ONLY").length).toBeGreaterThan(0);
    expect(screen.getByText("show interfaces status")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Run read-only check/ })).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("keeps all physical write controls disabled when write mode is off", () => {
    const setup: SetupStatus = {
      configured: true,
      hasPassword: false,
      hasEnableSecret: false,
      storage: "none",
      mockMode: true,
      enableWriteActions: false,
    };
    render(
      <SafeControlPanel
        setup={setup}
        interfaces={[interfaceStatus]}
        onChange={() => undefined}
      />,
    );
    const actionButtons = screen.getAllByRole("button").filter((button) => (
      /Enable port|Disable port|Set description|Enable PoE|Save config/i.test(button.textContent || "")
    ));
    expect(actionButtons.length).toBeGreaterThan(0);
    expect(actionButtons.every((button) => (button as HTMLButtonElement).disabled)).toBe(true);
    expect(screen.getByText(/Write actions are disabled/)).toBeTruthy();
  });
});
