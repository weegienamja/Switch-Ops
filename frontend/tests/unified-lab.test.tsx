import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import MerakiSettingsSection from "@/components/MerakiSettingsSection";
import MerakiTopologyOverlay from "@/components/MerakiTopologyOverlay";
import UnifiedLabPanel from "@/components/UnifiedLabPanel";
import { api } from "@/lib/api";
import type { MerakiSetupStatus, UnifiedLabState } from "@/lib/unifiedTypes";

const now = "2026-01-15T12:00:00Z";

const state: UnifiedLabState = {
  generatedAt: now,
  sourceHealth: [
    { provider: "catalyst-ios", state: "healthy", detail: "Catalyst complete.", checkedAt: now, lastSuccessAt: now, complete: true, failedOperations: [] },
    { provider: "meraki-dashboard", state: "partial", detail: "Meraki partial.", checkedAt: now, lastSuccessAt: now, complete: false, failedOperations: ["network_clients"] },
  ],
  providerEntities: [
    { id: "cat-ap", provider: "catalyst-ios", providerRef: "cat-ap", label: "AP on Gi0/4", category: "access-point", model: "MR44", identifiers: [{ kind: "name", protectedValue: "pid-name-synthetic000000000001", strength: "weak", provenanceRef: "cat-ap" }], claimIds: ["claim-cat-name"], observedAt: now, freshness: "current" },
    { id: "meraki-ap", provider: "meraki-dashboard", providerRef: "meraki-ap", label: "Synthetic MR44", category: "access-point", model: "MR44", identifiers: [{ kind: "name", protectedValue: "pid-name-synthetic000000000001", strength: "weak", provenanceRef: "meraki-ap" }], claimIds: ["claim-meraki-name"], observedAt: now, freshness: "current" },
  ],
  claims: [
    { id: "claim-cat-name", provider: "catalyst-ios", subjectRef: "cat-ap", field: "name", value: "AP on Gi0/4", strength: "weak", freshness: "current", detail: "Catalyst received an LLDP name.", provenance: { provider: "catalyst-ios", sourceKind: "lldp", sourceObjectRef: "cat-ap", scope: {}, observedAt: now, collectedAt: now, complete: true } },
    { id: "claim-meraki-name", provider: "meraki-dashboard", subjectRef: "meraki-ap", field: "name", value: "Synthetic MR44", strength: "weak", freshness: "current", detail: "Meraki inventory name.", provenance: { provider: "meraki-dashboard", sourceKind: "organization-device-inventory", sourceObjectRef: "meraki-ap", scope: {}, observedAt: now, collectedAt: now, complete: true } },
  ],
  entities: [
    { id: "unified-cat", label: "AP on Gi0/4", category: "access-point", providerEntityIds: ["cat-ap"], providers: ["catalyst-ios"], identityState: "AMBIGUOUS", freshness: "current", evidenceIds: ["claim-cat-name"], attributes: [
      { field: "identity", state: "AMBIGUOUS", providerValues: {}, claimIds: [], explanation: "Candidate only." },
      { field: "name", state: "PROVIDER_ONLY", value: "AP on Gi0/4", providerValues: { "catalyst-ios": "AP on Gi0/4" }, claimIds: ["claim-cat-name"], explanation: "Catalyst only." },
    ] },
    { id: "unified-meraki", label: "Synthetic MR44", category: "access-point", providerEntityIds: ["meraki-ap"], providers: ["meraki-dashboard"], identityState: "AMBIGUOUS", freshness: "current", evidenceIds: ["claim-meraki-name"], attributes: [
      { field: "identity", state: "AMBIGUOUS", providerValues: {}, claimIds: [], explanation: "Candidate only." },
    ] },
  ],
  identityLinks: [
    { id: "candidate-1", leftEntityId: "cat-ap", rightEntityId: "meraki-ap", state: "candidate", automatic: true, evaluatedAt: now, reasons: [{ kind: "hint", field: "name", strength: "weak", summary: "Names agree only as a hint.", provenanceRefs: ["cat-ap", "meraki-ap"] }] },
  ],
  conflicts: [
    { id: "conflict-1", leftEntityId: "cat-ap", rightEntityId: "meraki-ap", field: "serial", summary: "Different strong serial identifiers.", provenanceRefs: [] },
  ],
  relationships: [
    { id: "rel-1", subjectId: "unified-meraki", objectId: "unified-cat", relationship: "relationship", state: "PROVIDER_ONLY", providerClaimIds: ["claim-meraki-name"], explanation: "This relationship is supported by one provider only." },
  ],
};

afterEach(() => vi.restoreAllMocks());

describe("Unified Lab", () => {
  it("shows provider health, candidates, independent states, conflicts and provenance", () => {
    const onDecision = vi.fn();
    render(
      <UnifiedLabPanel
        state={state}
        onRefreshMeraki={() => undefined}
        onDecision={onDecision}
      />,
    );

    expect(screen.getAllByText("Catalyst").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Meraki").length).toBeGreaterThan(0);
    expect(screen.getAllByText("AMBIGUOUS").length).toBeGreaterThan(0);
    expect(screen.getByText("Different strong serial identifiers.")).toBeTruthy();
    expect(screen.getByText("organization-device-inventory", { exact: false })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Confirm local identity" }));
    expect(onDecision).toHaveBeenCalledWith("candidate-1", "confirm");
  });

  it("keeps Meraki evidence in a labelled overlay", () => {
    render(<MerakiTopologyOverlay state={state} />);
    expect(screen.getByText("Evidence the Catalyst-only drawing cannot prove")).toBeTruthy();
    expect(screen.getByText("Synthetic MR44")).toBeTruthy();
    expect(screen.getByText(/Catalyst geometry is unchanged/)).toBeTruthy();
  });
});

describe("Meraki source setup", () => {
  it("requires a deliberate key-entry action and stores through the typed API", async () => {
    const unconfigured: MerakiSetupStatus = {
      configured: false,
      keyringAvailable: true,
      storage: "none",
      selection: null,
      sourceHealth: {
        provider: "meraki-dashboard",
        state: "not-configured",
        detail: "Optional source.",
        checkedAt: now,
        complete: false,
        failedOperations: [],
      },
    };
    const configured: MerakiSetupStatus = { ...unconfigured, configured: true, storage: "keyring" };
    vi.spyOn(api, "merakiStatus").mockResolvedValue(unconfigured);
    const save = vi.spyOn(api, "saveMerakiApiKey").mockResolvedValue(configured);

    render(<MerakiSettingsSection />);
    const add = await screen.findByRole("button", { name: "Add Meraki evidence source" });
    expect(document.querySelector('input[type="password"]')).toBeNull();
    fireEvent.click(add);
    const input = screen.getByLabelText("Dashboard API key");
    fireEvent.change(input, { target: { value: "synthetic-key-value" } });
    fireEvent.click(screen.getByRole("button", { name: "Save key" }));

    await waitFor(() => expect(save).toHaveBeenCalledWith("synthetic-key-value"));
    expect(document.body.textContent).not.toContain("synthetic-key-value");
  });
});
