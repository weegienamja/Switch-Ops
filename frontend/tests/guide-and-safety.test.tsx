import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AdvancedOperationsPanel from "@/components/AdvancedOperationsPanel";
import LabGuide from "@/components/LabGuide";
import type { LiveOperationsController } from "@/lib/useLiveOperations";
import type { ChangeSession, GuideOperation, InterfaceStatus, NetworkInterface } from "@/lib/types";

const interfaceStatus: InterfaceStatus = {
  port: "Gi0/4",
  name: "Lab access point",
  status: "notconnect",
  vlan: "1",
  duplex: "auto",
  speed: "auto",
  type: "10/100/1000BaseTX",
  protected: false,
  policyState: "OPERABLE",
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
  deviceId: "switch-synthetic",
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
  policyState: "OPERABLE",
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
    changeSession: null,
    lock: { capability: false, unlocked: false },
    config: { runningModified: false, pendingOperations: 0, detail: "Configurations match." },
    lastEventAt: null,
    unlock: vi.fn(),
    lockNow: vi.fn(),
    runOperation: vi.fn(),
    runChangeSession: vi.fn(),
    save: vi.fn(),
    refreshConfig: vi.fn(),
    ...overrides,
  } as LiveOperationsController;
}

function changeSession(
  status: ChangeSession["status"] = "ready",
  controlPath: "clear" | "confirmed" = "clear",
): ChangeSession {
  return {
    id: "change-test",
    plan: {
      id: "plan-test",
      deviceId: "switch-synthetic",
      targetInterface: "Gi0/4",
      steps: [{ interface: "Gi0/4", kind: "admin_down" }],
      declaredIntent: {
        summary: "Administratively disable Gi0/4.",
        expectedPostconditions: [
          { category: "interface", field: "adminState", expectation: "down", required: true },
        ],
        unacceptableEffects: ["Any unrelated interface changes."],
      },
      createdAt: "2026-08-23T12:00:00Z",
    },
    status,
    preflight: {
      evaluatedAt: "2026-08-23T12:00:01Z",
      outcome: status === "blocked" ? "blocked" : "ready",
      checks: [
        {
          code: "control_path",
          label: "Control path",
          status: controlPath === "confirmed" ? "block" : "pass",
          detail: controlPath === "confirmed" ? "The local control path is on this port." : "No control-path evidence found.",
          evidence: [],
        },
      ],
      impact: {
        targetInterface: "Gi0/4",
        attachedEndpoints: 1,
        learnedBehind: 0,
        controlPath,
        controlPathDetail: controlPath === "confirmed" ? "Current evidence confirms the control path." : "No current evidence places the control path here.",
        confidenceLimitations: [],
      },
    },
    operationStages: [],
    outcomeDetail: status === "blocked" ? "Preflight blocked execution." : "Preflight passed.",
    createdAt: "2026-08-23T12:00:00Z",
    updatedAt: "2026-08-23T12:00:01Z",
  };
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
  it("allows read-only planning while write mode is off but offers no execution", () => {
    render(
      <AdvancedOperationsPanel
        selected={selected}
        live={liveController()}
      />,
    );
    const actionButtons = screen.getAllByRole("button").filter((button) =>
      /Review enable|Review disable|Review PoE auto|Review PoE off|Review/i.test(button.textContent || ""),
    );
    expect(actionButtons.length).toBeGreaterThan(0);
    expect(actionButtons.some((button) => !(button as HTMLButtonElement).disabled)).toBe(true);
    expect(screen.queryByRole("button", { name: /Execute reviewed change/i })).toBeNull();
    expect(screen.getByText(/change plans and preflight evidence remain available/i)).toBeTruthy();
  });

  it("states the generalized policy boundary without naming a private port layout", () => {
    render(
      <AdvancedOperationsPanel
        selected={selected}
        live={liveController()}
      />,
    );
    expect(screen.getByText("CONTROL LOCKED")).toBeTruthy();
    expect(screen.getByText("Read-only installation")).toBeTruthy();
    expect(screen.getByText(/Protected and unmanaged interfaces cannot be changed/)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/Gi0\/1, Gi0\/2 and Vlan1/);
  });

  it("allows protected-interface assessment but never offers execution", () => {
    render(
      <AdvancedOperationsPanel
        selected={{ ...selected, port: "Gi0/1", protected: true, policyState: "PROTECTED" }}
        live={liveController({ lock: { capability: true, unlocked: true } })}
      />,
    );
    expect(screen.getByText(/protected by the local device policy/i)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Review enable" }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole("button", { name: "Review disable" }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByRole("button", { name: /Execute reviewed change/i })).toBeNull();
  });

  it("shows ready preflight evidence but keeps Execute disabled while locked", () => {
    render(
      <AdvancedOperationsPanel
        selected={selected}
        live={liveController({
          lock: { capability: true, unlocked: false },
          changeSession: changeSession("ready"),
        })}
      />,
    );
    expect(screen.getByText("READY")).toBeTruthy();
    expect(screen.getByText(/No current evidence places the control path here/)).toBeTruthy();
    expect((screen.getByRole("button", { name: /Execute reviewed change/i }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Unlock control before execution/)).toBeTruthy();
  });

  it("surfaces a confirmed control-path blocker and never offers Execute", () => {
    render(
      <AdvancedOperationsPanel
        selected={selected}
        live={liveController({
          lock: { capability: true, unlocked: true },
          changeSession: changeSession("blocked", "confirmed"),
        })}
      />,
    );
    expect(screen.getByText("BLOCKED")).toBeTruthy();
    expect(screen.getAllByText(/Current evidence confirms the control path/).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /Execute reviewed change/i })).toBeNull();
    expect(screen.getByText(/No IOS configuration was attempted/)).toBeTruthy();
  });
});
