import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ChangeHistoryPanel from "@/components/ChangeHistoryPanel";
import type { ChangeSession } from "@/lib/types";


function session(status: ChangeSession["status"]): ChangeSession {
  return {
    id: `change-${status}`,
    plan: {
      id: "plan-synthetic",
      deviceId: "switch-synthetic",
      targetInterface: "Gi0/6",
      steps: [{ interface: "Gi0/6", kind: "admin_up" }],
      declaredIntent: {
        summary: "Administratively enable Gi0/6.",
        expectedPostconditions: [
          { category: "interface", field: "adminState", expectation: "up", required: true },
        ],
        unacceptableEffects: [],
      },
      createdAt: "2026-08-23T12:00:00Z",
    },
    status,
    operationStages: [],
    outcomeDetail:
      status === "indeterminate"
        ? "SwitchOps could not prove the final device state."
        : "The requested state was achieved.",
    createdAt: "2026-08-23T12:00:00Z",
    updatedAt: "2026-08-23T12:01:00Z",
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("Change Assurance history", () => {
  it("retains indeterminate as an honest terminal state", async () => {
    const indeterminate = session("indeterminate");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        statusText: "OK",
        json: async () => ({ sessions: [indeterminate] }),
      })) as unknown as typeof fetch,
    );

    render(<ChangeHistoryPanel active={indeterminate} />);

    await waitFor(() => expect(screen.getByText("INDETERMINATE")).toBeTruthy());
    expect(screen.getByText("Administratively enable Gi0/6.")).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/rollback succeeded/i);
  });

  it("explains that blocked plans are retained even when nothing executed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        statusText: "OK",
        json: async () => ({ sessions: [] }),
      })) as unknown as typeof fetch,
    );

    render(<ChangeHistoryPanel />);

    await waitFor(() => expect(screen.getByText(/Creating a plan records it even if preflight blocks execution/)).toBeTruthy());
  });
});
