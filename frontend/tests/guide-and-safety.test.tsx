import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import AdvancedOperationsPanel from "@/components/AdvancedOperationsPanel";
import LabGuide from "@/components/LabGuide";
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

const operations: GuideOperation[] = [
  {
    id: "connected_ports",
    category: "GETTING STARTED",
    title: "Check connected ports",
    question: "Which ports are connected?",
    whatItTellsYou: "Explains which physical interfaces have a link.",
    safety: "READ ONLY",
    commands: ["show interfaces status"],
    requiresInterface: false,
  },
  {
    id: "port_state",
    category: "TROUBLESHOOTING",
    title: "Explain a port state",
    question: "Why is this port down?",
    whatItTellsYou: "Distinguishes a disabled port from one with no link.",
    safety: "READ ONLY",
    commands: ["show interfaces status", "show power inline"],
    requiresInterface: true,
  },
];

const readOnlySetup: SetupStatus = {
  configured: true,
  hasPassword: false,
  hasEnableSecret: false,
  storage: "none",
  mockMode: true,
  enableWriteActions: false,
};

describe("Lab Guide stays read-only learning", () => {
  it("renders an allowlisted operation, exact command, and read-only boundary", () => {
    render(<LabGuide operations={operations} interfaces={[interfaceStatus]} />);
    expect(screen.getAllByText("READ ONLY").length).toBeGreaterThan(0);
    expect(screen.getByText("show interfaces status")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Run read-only check/ })).toBeTruthy();
  });

  it("offers no free-text command entry anywhere in the guide", () => {
    render(<LabGuide operations={operations} interfaces={[interfaceStatus]} />);
    expect(screen.queryByRole("textbox")).toBeNull();
    // Interfaces are chosen from a bounded list, never typed.
    for (const combo of screen.queryAllByRole("combobox")) {
      expect(combo.tagName).toBe("SELECT");
    }
  });

  it("no longer exposes device write controls beside the beginner questions", () => {
    render(<LabGuide operations={operations} interfaces={[interfaceStatus]} />);
    for (const label of [/Enable port/i, /Disable port/i, /Enable PoE/i, /Write memory/i, /Set description/i]) {
      expect(screen.queryByRole("button", { name: label })).toBeNull();
    }
    expect(screen.queryByText(/write memory/i)).toBeNull();
  });

  it("keeps every beginner question phrased as a question", () => {
    render(<LabGuide operations={operations} interfaces={[interfaceStatus]} />);
    expect(screen.getByRole("button", { name: "Which ports are connected?" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Why is this port down?" })).toBeTruthy();
  });
});

describe("advanced operations", () => {
  it("keeps all physical write controls disabled when write mode is off", () => {
    render(
      <AdvancedOperationsPanel
        setup={readOnlySetup}
        interfaces={[interfaceStatus]}
        onChange={() => undefined}
      />,
    );
    const actionButtons = screen.getAllByRole("button").filter((button) =>
      /Enable port|Disable port|Enable PoE|Write memory|Apply/i.test(button.textContent || ""),
    );
    expect(actionButtons.length).toBeGreaterThan(0);
    expect(actionButtons.every((button) => (button as HTMLButtonElement).disabled)).toBe(true);
  });

  it("states the write state in words and names the protected interfaces", () => {
    render(
      <AdvancedOperationsPanel
        setup={readOnlySetup}
        interfaces={[interfaceStatus]}
        onChange={() => undefined}
      />,
    );
    expect(screen.getByText("WRITES DISABLED")).toBeTruthy();
    expect(screen.getByText(/SwitchOps cannot change this switch/)).toBeTruthy();
    expect(screen.getByText(/Gi0\/1, Gi0\/2 and Vlan1 stay protected/)).toBeTruthy();
  });

  it("never offers an apply action for the protected interfaces", () => {
    render(
      <AdvancedOperationsPanel
        setup={{ ...readOnlySetup, enableWriteActions: true }}
        interfaces={[
          { ...interfaceStatus, port: "Gi0/1", name: "Uplink to Test Gateway", protected: true },
          { ...interfaceStatus, port: "Gi0/2", name: "Test Workstation", protected: true },
          interfaceStatus,
        ]}
        onChange={() => undefined}
      />,
    );
    const options = screen.getAllByRole("option").map((option) => option.textContent || "");
    expect(options.some((option) => option.startsWith("Gi0/4"))).toBe(true);
    expect(options.some((option) => option.startsWith("Gi0/1"))).toBe(false);
    expect(options.some((option) => option.startsWith("Gi0/2"))).toBe(false);
  });
});
