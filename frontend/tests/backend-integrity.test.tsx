import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/dynamic", () => ({
  default: () => function DeferredPanel() { return null; },
}));

vi.mock("@/lib/useLiveOperations", () => ({
  useLiveOperations: () => ({
    interfaces: [],
    freshness: { deep: "2026-08-25T10:00:00Z" },
    connection: { state: "live", queueDepth: 0, lastSuccessAt: "2026-08-25T10:00:00Z" },
    streamState: "open",
    operation: null,
    changeSession: null,
    lock: { capability: false, unlocked: false },
    config: { runningModified: false, pendingOperations: 0, detail: "unchanged" },
    lastEventAt: null,
    lldp: null,
    topology: null,
  }),
}));

// The shell's verdict is delivered by a Tauri event. Drive it directly.
let emit: ((event: unknown) => void) | null = null;
let pulledVerdict: unknown = null;
vi.mock("@/lib/backendIntegrity", async () => {
  const actual = await vi.importActual<typeof import("@/lib/backendIntegrity")>(
    "@/lib/backendIntegrity",
  );
  return {
    ...actual,
    getBackendVerification: async () =>
      pulledVerdict ? actual.parseBackendUnverified(pulledVerdict) : null,
    onBackendUnverified: async (handler: (event: unknown) => void) => {
      emit = handler;
      return () => {
        emit = null;
      };
    },
  };
});

import DashboardShell from "@/components/DashboardShell";
import BackendUnverifiedNotice from "@/components/BackendUnverifiedNotice";
import { api, ApiError } from "@/lib/api";
import {
  describeBackendUnverified,
  parseBackendUnverified,
  type BackendUnverifiedReason,
} from "@/lib/backendIntegrity";

afterEach(() => {
  emit = null;
  pulledVerdict = null;
  cleanup();
  vi.restoreAllMocks();
});

const REASONS: BackendUnverifiedReason[] = [
  "PORT_ALREADY_BOUND",
  "BACKEND_TOO_OLD",
  "FOREIGN_BACKEND",
  "NO_RESPONSE",
];

describe("backend-unverified payload parsing", () => {
  it.each(REASONS)("accepts the typed reason %s", (reason) => {
    expect(parseBackendUnverified({ reason })?.reason).toBe(reason);
  });

  it("treats an unknown reason as a failure rather than ignoring it", () => {
    // Failing open here would restore the silent fallback this mechanism
    // exists to prevent.
    expect(parseBackendUnverified({ reason: "SOMETHING_NEW" })?.reason).toBe("NO_RESPONSE");
  });

  it("rejects a non-object payload", () => {
    expect(parseBackendUnverified("free-form string")).toBeNull();
    expect(parseBackendUnverified(null)).toBeNull();
  });

  it("keeps safe observed provenance and nothing else", () => {
    const parsed = parseBackendUnverified({
      reason: "FOREIGN_BACKEND",
      observed: {
        buildId: "abc123def456",
        apiSchemaVersion: 2,
        runtimeMode: "development",
        startedAt: "2026-08-26T00:00:00Z",
        sidecarToken: "must-not-survive",
      },
    });
    expect(parsed?.observed?.buildId).toBe("abc123def456");
    expect(JSON.stringify(parsed)).not.toContain("must-not-survive");
  });

  it("describes every reason in operator language", () => {
    for (const reason of REASONS) {
      expect(describeBackendUnverified(reason).length).toBeGreaterThan(10);
    }
  });
});

describe("BackendUnverifiedNotice", () => {
  it("names the fault as local runtime integrity, not a device problem", () => {
    render(
      <BackendUnverifiedNotice state={{ reason: "FOREIGN_BACKEND", observed: null }} />,
    );
    expect(screen.getByText("Backend verification failed")).toBeTruthy();
    expect(screen.getByText("FOREIGN_BACKEND")).toBeTruthy();
    expect(screen.getByText(/not a Catalyst fault/i)).toBeTruthy();
    // It must not borrow any network or device vocabulary.
    expect(screen.queryByText(/CATALYST UNAVAILABLE/i)).toBeNull();
    expect(screen.queryByText(/DEVICE OFFLINE/i)).toBeNull();
    expect(screen.queryByText(/MANAGEMENT PATH DEGRADED/i)).toBeNull();
  });

  it("shows safe provenance of the other backend but never a token", () => {
    render(
      <BackendUnverifiedNotice
        state={{
          reason: "FOREIGN_BACKEND",
          observed: {
            buildId: "9eab64d9d4d1",
            apiSchemaVersion: 2,
            runtimeMode: "development",
            startedAt: "2026-08-26T16:13:41Z",
          },
        }}
      />,
    );
    expect(screen.getByText(/build 9eab64d9d4d1/)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/token|nonce/i);
  });

  it("offers no retry, because retrying would re-read the rejected process", () => {
    render(
      <BackendUnverifiedNotice state={{ reason: "PORT_ALREADY_BOUND", observed: null }} />,
    );
    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
  });
});

describe("DashboardShell under an unverified backend", () => {
  it("renders the integrity state and stops trusting fetched data", async () => {
    vi.spyOn(api, "setupStatus").mockRejectedValue(new Error("must not matter"));
    render(<DashboardShell />);
    await waitFor(() => expect(emit).not.toBeNull());

    act(() => emit?.({ reason: "PORT_ALREADY_BOUND" }));

    expect(await screen.findByText("Backend verification failed")).toBeTruthy();
    expect(screen.getByText("PORT_ALREADY_BOUND")).toBeTruthy();
  });

  it("does not present an unverified backend as a device failure", async () => {
    vi.spyOn(api, "dashboard").mockRejectedValue(
      new ApiError({
        status: 502,
        code: "switch_unreachable",
        message: "The configured Catalyst could not be reached.",
        detail: "No device-side cause was assumed.",
        category: "DEVICE_UNREACHABLE",
      }),
    );
    vi.spyOn(api, "setupStatus").mockRejectedValue(new Error("unused"));

    render(<DashboardShell />);
    await waitFor(() => expect(emit).not.toBeNull());
    act(() => emit?.({ reason: "FOREIGN_BACKEND" }));

    expect(await screen.findByText("Backend verification failed")).toBeTruthy();
    // The device error must not win over the integrity state.
    expect(screen.queryByText(/could not be reached/i)).toBeNull();
    expect(screen.queryByText(/MANAGEMENT PATH DEGRADED/i)).toBeNull();
  });
  it("asks the shell on mount, so a missed event cannot strand the dashboard", async () => {
    // The shell records its verdict before showing the window and the webview
    // may register its listener afterwards, so the event alone can be missed.
    // Asking on mount is what makes this reliable.
    pulledVerdict = { reason: "PORT_ALREADY_BOUND" };
    const dashboard = vi.spyOn(api, "setupStatus").mockResolvedValue({} as never);

    render(<DashboardShell />);

    expect(await screen.findByText("Backend verification failed")).toBeTruthy();
    // No event was ever emitted in this test.
    expect(screen.getByText("PORT_ALREADY_BOUND")).toBeTruthy();
    // And the dashboard must not have queried the rejected backend at all.
    expect(dashboard).not.toHaveBeenCalled();
  });

  it("loads normally when the shell reports a verified backend", async () => {
    pulledVerdict = null;
    const setup = vi.spyOn(api, "setupStatus").mockRejectedValue(new Error("stop here"));
    render(<DashboardShell />);
    await waitFor(() => expect(setup).toHaveBeenCalled());
    expect(screen.queryByText("Backend verification failed")).toBeNull();
  });
});
