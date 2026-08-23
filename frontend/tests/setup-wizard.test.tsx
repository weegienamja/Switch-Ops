import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import SetupWizard from "@/components/SetupWizard";
import { api } from "@/lib/api";

afterEach(() => vi.restoreAllMocks());

describe("clean first-run setup", () => {
  it("starts with no private device or username defaults", () => {
    render(<SetupWizard onComplete={() => undefined} />);

    expect((screen.getByLabelText("Management IP or hostname") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Username") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Password") as HTMLInputElement).value).toBe("");
    expect(screen.getByText("No Catalyst configured")).toBeTruthy();
  });

  it("does not enter the dashboard when the real connection test fails", async () => {
    vi.spyOn(api, "saveCredentials").mockResolvedValue({
      configured: true,
      hasPassword: true,
      hasEnableSecret: false,
      storage: "keyring",
      mockMode: false,
      enableWriteActions: false,
      switchHost: "192.0.2.10",
      switchUsername: "network-operator",
      switchDeviceType: "cisco_ios",
    });
    vi.spyOn(api, "testConnection").mockResolvedValue({
      ok: false,
      mode: "real",
      failureCode: "unreachable",
      summary: "The configured switch did not accept a TCP connection on port 22.",
      checks: [],
      testedAt: "2026-08-23T00:00:00Z",
      durationMs: 10,
    });
    const complete = vi.fn();
    render(<SetupWizard onComplete={complete} />);

    fireEvent.change(screen.getByLabelText("Management IP or hostname"), {
      target: { value: "192.0.2.10" },
    });
    fireEvent.change(screen.getByLabelText("Username"), {
      target: { value: "network-operator" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "synthetic-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save and test connection" }));

    await waitFor(() => expect(api.testConnection).toHaveBeenCalledTimes(1));
    expect(complete).not.toHaveBeenCalled();
    expect(screen.getByText("Connection failed")).toBeTruthy();
    expect(screen.getByText(/credentials remain stored locally/i)).toBeTruthy();
  });
});
