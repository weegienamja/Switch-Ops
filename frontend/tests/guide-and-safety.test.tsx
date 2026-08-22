import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AdvancedOperationsPanel from "@/components/AdvancedOperationsPanel";
import LabGuide from "@/components/LabGuide";
import type { LiveOperationsController } from "@/lib/useLiveOperations";
import type { GuideOperation, InterfaceStatus, NetworkInterface } from "@/lib/types";

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

const selected: NetworkInterface = {
  id: "if-gi04",
  deviceId: "switch-lab",
  port: "Gi0/4",
  description: "Lab access point",
  adminState: "up",
  operState: "down",
  speed: "auto",
  duplex: "auto",
  vlan: "1",
  poeCapable: true,
  poeState: "off",
  poeWatts: 0,
  protected: false,
  role: "access",
  learnedMacCount: 0,
};

function liveController(
  overrides: Partial<LiveOperationsController> = {},
): LiveOperationsController {
  return {
    interfaces: [],
    freshness: {},
    connection: { state: "live", queueDepth: 0 },
    streamState: "open",
    operation: null,
    lock: { capability: false, unlocked: false },
    config: { runningModified: false, pendingOperations: 0, detail: "Configurations match." },
    lastEventAt: null,
    unlock: vi.fn(),
    lockNow: vi.fn(),
    runOperation: vi.fn(),
    save: vi.fn(),
    refreshConfig: vi.fn(),
    ...overrides,
  } as LiveOperationsController;
}

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
        selected={selected}
        live={liveController()}
      />,
    );
    const actionButtons = screen.getAllByRole("button").filter((button) =>
      /Enable port|Disable port|PoE Auto|PoE Off|Review/i.test(button.textContent || ""),
    );
    expect(actionButtons.length).toBeGreaterThan(0);
    expect(actionButtons.every((button) => (button as HTMLButtonElement).disabled)).toBe(true);
  });

  it("states the write state in words and names the protected interfaces", () => {
    render(
      <AdvancedOperationsPanel
        selected={selected}
        live={liveController()}
      />,
    );
    expect(screen.getByText("CONTROL LOCKED")).toBeTruthy();
    expect(screen.getByText("Read-only installation")).toBeTruthy();
    expect(screen.getByText(/Gi0\/1, Gi0\/2 and Vlan1 stay protected/)).toBeTruthy();
  });

  it("never offers an apply action for the protected interfaces", () => {
    render(
      <AdvancedOperationsPanel
        selected={{ ...selected, port: "Gi0/1", protected: true }}
        live={liveController({ lock: { capability: true, unlocked: true } })}
      />,
    );
    expect(screen.getByText(/management interface is protected/i)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Enable port" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Disable port" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
