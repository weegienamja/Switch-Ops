import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SettingsPanel from "@/components/SettingsPanel";
import { api } from "@/lib/api";
import type { ConnectionTestResult, InterfacePolicyResponse, RuntimeInfo, SetupStatus } from "@/lib/types";

const runtimeInfo: RuntimeInfo = {
  version: "0.2.1",
  apiHost: "127.0.0.1",
  apiPort: 8765,
  mockMode: false,
  enableWriteActions: false,
  legacySsh: true,
  apiDocsEnabled: false,
  hostKeyPinned: true,
  telemetryRetentionDays: 30,
  telemetryCollection: "live-tiered",
  dataDir: "C:\\Synthetic\\SwitchOps\\data",
  backupDir: "C:\\Synthetic\\SwitchOps\\backups",
  logDir: "C:\\Synthetic\\SwitchOps\\logs",
  corsOrigins: ["tauri://localhost"],
  deviceDriver: "cisco_ios",
};

const realSetup: SetupStatus = {
  configured: true,
  hasPassword: true,
  hasEnableSecret: true,
  storage: "keyring",
  mockMode: false,
  enableWriteActions: false,
  switchHost: "192.0.2.10",
  switchUsername: "synthetic-user",
  switchDeviceType: "cisco_ios",
};

const localPolicy: InterfacePolicyResponse = {
  deviceConfigured: true,
  deviceKey: "0".repeat(64),
  valid: true,
  controlledWritesEnabled: false,
  interfaces: [
    { interface: "Gi1/0/1", state: "PROTECTED" },
    { interface: "Gi1/0/2", state: "UNMANAGED" },
  ],
};

function healthyTest(overrides: Partial<ConnectionTestResult> = {}): ConnectionTestResult {
  return {
    ok: true,
    mode: "real",
    summary: "Connection healthy. SwitchOps can authenticate and read this switch. Nothing was changed.",
    checks: [
      { id: "credentials", label: "Stored credentials available", status: "pass", detail: "Windows Credential Manager holds a username and password for this switch." },
      { id: "reachable", label: "Host reachable on TCP 22", status: "pass", detail: "192.0.2.10 accepted a TCP connection on port 22." },
      { id: "ssh", label: "SSH session established", status: "pass", detail: "An SSH session was negotiated and opened." },
      { id: "host_key", label: "SSH host key matched", status: "pass", detail: "The presented host key matched the key SwitchOps pinned on first use." },
      { id: "auth", label: "Authentication succeeded", status: "pass", detail: "The stored account was accepted by the switch." },
      { id: "platform", label: "Cisco IOS detected", status: "pass", detail: "Reported as WS-C3560CG-8PC-S running IOS 12.2(55)EX2." },
      { id: "read_ops", label: "Read-only operations available", status: "pass", detail: "`show interfaces status` returned 10 interface rows." },
    ],
    testedAt: "2026-08-22T04:00:00Z",
    durationMs: 1840,
    ...overrides,
  };
}

beforeEach(() => {
  vi.spyOn(api, "systemInfo").mockResolvedValue(runtimeInfo);
  vi.spyOn(api, "interfacePolicy").mockResolvedValue(localPolicy);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("settings wording", () => {
  it("names the credential store in plain English, never 'keyring'", async () => {
    render(<SettingsPanel setup={realSetup} onClose={() => undefined} onChange={() => undefined} />);
    expect(screen.getAllByText("Windows Credential Manager").length).toBeGreaterThan(0);
    expect(screen.queryByText("keyring")).toBeNull();
    expect(
      screen.getByText(/Stored in the Windows secure credential store/),
    ).toBeTruthy();
    await waitFor(() => expect(screen.getByText("30 days")).toBeTruthy());
  });

  it("shows the platform to the user and keeps the driver id in Advanced", async () => {
    render(<SettingsPanel setup={realSetup} onClose={() => undefined} onChange={() => undefined} />);
    expect(screen.getAllByText("Cisco IOS").length).toBeGreaterThan(0);
    const advanced = document.querySelector(".settings-advanced") as HTMLDetailsElement;
    expect(advanced).toBeTruthy();
    expect(advanced.open).toBe(false);
    await waitFor(() => expect(screen.getByText("cisco_ios")).toBeTruthy());
    // The raw driver id lives inside the collapsed Advanced block only.
    expect(advanced.contains(screen.getByText("cisco_ios"))).toBe(true);
  });

  it("states operation mode as labelled states rather than yes/no", async () => {
    render(<SettingsPanel setup={realSetup} onClose={() => undefined} onChange={() => undefined} />);
    const modes = within(document.querySelector(".settings-modes") as HTMLElement);
    expect(modes.getByText("Device connection")).toBeTruthy();
    expect(modes.getByText("Configured")).toBeTruthy();
    expect(modes.getByText("Write operations")).toBeTruthy();
    expect(modes.getByText("Disabled")).toBeTruthy();
    expect(
      screen.getByText(/controlled writes are globally disabled/),
    ).toBeTruthy();
    // No bare yes/no anywhere.
    expect(screen.queryByText(/^(yes|no)$/i)).toBeNull();
    await waitFor(() => expect(screen.getByText("30 days")).toBeTruthy());
  });

  it("does not expose the source-only mock harness as a production setting", async () => {
    render(
      <SettingsPanel
        setup={{ ...realSetup, mockMode: true }}
        onClose={() => undefined}
        onChange={() => undefined}
      />,
    );
    expect(screen.queryByText("Mock mode")).toBeNull();
    expect(screen.queryByText(/recorded sample output/)).toBeNull();
    await waitFor(() => expect(screen.getByText("30 days")).toBeTruthy());
  });

  it("reports the security boundaries the product actually enforces", async () => {
    render(<SettingsPanel setup={realSetup} onClose={() => undefined} onChange={() => undefined} />);
    expect(screen.getByText("Localhost only")).toBeTruthy();
    expect(screen.getByText("Unavailable")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("Pinned")).toBeTruthy());
    expect(screen.getByText(/Windows SSH configuration is untouched/)).toBeTruthy();
  });

  it("describes tiered live telemetry with a retention window", async () => {
    render(<SettingsPanel setup={realSetup} onClose={() => undefined} onChange={() => undefined} />);
    expect(screen.getByText("Tiered live")).toBeTruthy();
    expect(screen.getByText(/Ports update every few seconds/)).toBeTruthy();
    await waitFor(() => expect(screen.getByText("30 days")).toBeTruthy());
  });
});

describe("local interface policy", () => {
  it("requires a second deliberate action before enabling controlled writes", async () => {
    const enabled = { ...localPolicy, controlledWritesEnabled: true };
    const setWrites = vi.spyOn(api, "setControlledWrites").mockResolvedValue(enabled);
    render(<SettingsPanel setup={realSetup} onClose={() => undefined} onChange={() => undefined} />);

    await waitFor(() => expect(screen.getByText("Gi1/0/1")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Review enabling writes" }));
    expect(setWrites).not.toHaveBeenCalled();
    expect(screen.getByText(/separate session unlock/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Confirm enable" }));
    await waitFor(() => expect(setWrites).toHaveBeenCalledWith(true));
  });

  it("applies a per-interface state through the policy API", async () => {
    const updated = {
      ...localPolicy,
      interfaces: localPolicy.interfaces.map((entry) =>
        entry.interface === "Gi1/0/2" ? { ...entry, state: "OPERABLE" as const } : entry,
      ),
    };
    const setPolicy = vi.spyOn(api, "setInterfacePolicy").mockResolvedValue(updated);
    render(<SettingsPanel setup={realSetup} onClose={() => undefined} onChange={() => undefined} />);

    const select = await waitFor(() => screen.getByLabelText("Policy for Gi1/0/2"));
    fireEvent.change(select, { target: { value: "OPERABLE" } });
    const row = select.closest(".interface-policy-row") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(setPolicy).toHaveBeenCalledWith("Gi1/0/2", "OPERABLE"));
  });
});

describe("test connection", () => {
  it("renders each check and the limits of what it proved", async () => {
    vi.spyOn(api, "testConnection").mockResolvedValue(healthyTest());
    render(<SettingsPanel setup={realSetup} onClose={() => undefined} onChange={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    await waitFor(() => expect(screen.getByText(/Connection healthy/)).toBeTruthy());
    expect(screen.getAllByText("PASS")).toHaveLength(7);
    expect(screen.getByText("Authentication succeeded")).toBeTruthy();
    expect(
      screen.getByText(/does not check Internet access, privilege level, or switch health/),
    ).toBeTruthy();
  });

  it("shows a host-key change as a blocked step, not a generic error", async () => {
    vi.spyOn(api, "testConnection").mockResolvedValue(
      healthyTest({
        ok: false,
        failureCode: "host_key_changed",
        summary: "Blocked for safety: the SSH host key changed.",
        checks: [
          { id: "credentials", label: "Stored credentials available", status: "pass", detail: "ok" },
          { id: "reachable", label: "Host reachable on TCP 22", status: "pass", detail: "ok" },
          { id: "ssh", label: "SSH session established", status: "pass", detail: "ok" },
          { id: "host_key", label: "SSH host key matched", status: "fail", detail: "The switch presented a different SSH host key." },
          { id: "auth", label: "Authentication succeeded", status: "skipped", detail: "Not reached." },
          { id: "platform", label: "Cisco IOS detected", status: "skipped", detail: "Not reached." },
          { id: "read_ops", label: "Read-only operations available", status: "skipped", detail: "Not reached." },
        ],
      }),
    );
    render(<SettingsPanel setup={realSetup} onClose={() => undefined} onChange={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    await waitFor(() => expect(screen.getByText(/Blocked for safety/)).toBeTruthy());
    expect(screen.getByText("FAIL")).toBeTruthy();
    expect(screen.getAllByText("SKIP")).toHaveLength(3);
    expect(document.querySelector(".conn-test--bad")).toBeTruthy();
  });

  it("surfaces a transport failure without crashing", async () => {
    vi.spyOn(api, "testConnection").mockRejectedValue(new Error("502 switch_connection_error"));
    render(<SettingsPanel setup={realSetup} onClose={() => undefined} onChange={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("502"));
  });
});

describe("clear credentials", () => {
  it("requires an explicit confirmation before clearing", async () => {
    const clear = vi.spyOn(api, "clearCredentials").mockResolvedValue(realSetup);
    render(<SettingsPanel setup={realSetup} onClose={() => undefined} onChange={() => undefined} />);

    fireEvent.click(screen.getByRole("button", { name: "Clear credentials" }));
    expect(clear).not.toHaveBeenCalled();
    expect(screen.getByText(/removes the stored SwitchOps login/)).toBeTruthy();
    expect(screen.getByText(/switch\s+configuration will not be changed/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Yes, clear credentials" }));
    await waitFor(() => expect(clear).toHaveBeenCalledTimes(1));
  });

  it("lets the confirmation be cancelled without clearing anything", async () => {
    const clear = vi.spyOn(api, "clearCredentials").mockResolvedValue(realSetup);
    render(<SettingsPanel setup={realSetup} onClose={() => undefined} onChange={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Clear credentials" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(clear).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Clear credentials" })).toBeTruthy();
    await waitFor(() => expect(screen.getByText("30 days")).toBeTruthy());
  });

  it("never asks the user to retype a password to clear one", async () => {
    render(<SettingsPanel setup={realSetup} onClose={() => undefined} onChange={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Clear credentials" }));
    expect(document.querySelector('input[type="password"]')).toBeNull();
    await waitFor(() => expect(screen.getByText("30 days")).toBeTruthy());
  });
});

describe("settings never leak secrets", () => {
  it("shows no password or enable secret anywhere in the dialog", async () => {
    render(
      <SettingsPanel
        setup={{ ...realSetup, hasPassword: true, hasEnableSecret: true }}
        onClose={() => undefined}
        onChange={() => undefined}
      />,
    );
    const text = document.body.textContent || "";
    expect(text).not.toMatch(/password\s*[:=]\s*\S/i);
    expect(document.querySelectorAll('input[type="password"]')).toHaveLength(0);
    await waitFor(() => expect(screen.getByText("30 days")).toBeTruthy());
  });
});
