import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { visibleConnectionState } from "@/components/LiveStatusBadge";
import { mergeLiveInterfaces } from "@/lib/live";
import { useLiveOperations } from "@/lib/useLiveOperations";
import type { LiveSnapshot, PoeResponse, TopologyModel } from "@/lib/types";

const topology: TopologyModel = {
  generatedAt: "2026-08-22T00:00:00Z",
  rootDeviceId: "switch",
  devices: [],
  interfaces: [
    {
      id: "if-6",
      deviceId: "switch",
      port: "Gi0/6",
      description: "old",
      adminState: "down",
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
    },
  ],
  links: [
    {
      id: "link-6",
      fromDeviceId: "switch",
      fromInterface: "Gi0/6",
      toDeviceId: "endpoint",
      status: "down",
      speed: "auto",
      poe: false,
      confidence: "low",
      evidence: [],
      evidenceLevel: "unknown",
      learnedMacCount: 0,
    },
  ],
};

const poe: PoeResponse = {
  availableWatts: 124,
  usedWatts: 0,
  remainingWatts: 124,
  ports: [
    { interface: "Gi0/6", admin: "auto", oper: "off", powerWatts: 0, device: "n/a", class: "n/a", maxWatts: 30 },
  ],
};

describe("live telemetry overlay", () => {
  it("updates the chassis-facing topology without discarding deep evidence", () => {
    const merged = mergeLiveInterfaces(
      topology,
      { interfaces: [{ port: "Gi0/6", name: "old", status: "disabled", vlan: "1", duplex: "auto", speed: "auto", type: "copper", protected: false }] },
      poe,
      [{ port: "Gi0/6", description: "live", status: "connected", admin_state: "up", oper_state: "up", speed: "a-1000", duplex: "a-full", vlan: "20", poe_state: "on", poe_watts: 8.4, protected: false }],
    );
    expect(merged.topology.interfaces[0]).toMatchObject({
      description: "live",
      adminState: "up",
      operState: "up",
      vlan: "20",
      learnedMacCount: 0,
    });
    expect(merged.topology.links[0].status).toBe("up");
    expect(merged.interfaces.interfaces[0].status).toBe("connected");
    expect(merged.poe.ports[0]).toMatchObject({ oper: "on", powerWatts: 8.4 });
  });

  it("shows all four user-facing connection states", () => {
    expect(visibleConnectionState({ state: "live", queueDepth: 0 }, "open")).toBe("LIVE");
    expect(visibleConnectionState({ state: "stale", queueDepth: 0 }, "open")).toBe("STALE");
    expect(visibleConnectionState({ state: "live", queueDepth: 0 }, "error")).toBe("RECONNECTING");
    expect(visibleConnectionState({ state: "offline", queueDepth: 0 }, "open")).toBe("OFFLINE");
  });
});

class FakeEventSource {
  static latest: FakeEventSource | null = null;
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  private listeners = new Map<string, Array<(event: Event) => void>>();

  constructor(_url: string) {
    FakeEventSource.latest = this;
  }

  addEventListener(name: string, listener: EventListenerOrEventListenerObject) {
    const callback = typeof listener === "function" ? listener : listener.handleEvent.bind(listener);
    this.listeners.set(name, [...(this.listeners.get(name) || []), callback]);
  }

  emit(name: string, data: unknown) {
    const event = new MessageEvent(name, { data: JSON.stringify({ at: "2026-08-22T00:00:00Z", data }) });
    for (const listener of this.listeners.get(name) || []) listener(event);
  }

  close() {}
}

function LiveProbe() {
  const live = useLiveOperations(true);
  return (
    <div>
      <span data-testid="connection">{live.connection.state}</span>
      <span data-testid="port">{live.interfaces[0]?.port || "none"}</span>
    </div>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  FakeEventSource.latest = null;
});

describe("SSE subscription", () => {
  it("accepts a typed snapshot event and updates live state", async () => {
    const initial: LiveSnapshot = {
      interfaces: [],
      poe: { usedW: 0, availableW: 124 },
      freshness: {},
      operationInProgress: null,
      connection: { state: "offline", queueDepth: 0 },
    };
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
      const requestUrl = String(input);
      const data = requestUrl.includes("/api/live/state")
        ? initial
        : requestUrl.includes("/api/control/lock")
          ? { capability: false, unlocked: false }
          : { runningModified: false, pendingOperations: 0, detail: "Configurations match." };
      return { ok: true, statusText: "OK", json: async () => data } as Response;
    }));

    render(<LiveProbe />);
    await waitFor(() => expect(FakeEventSource.latest).not.toBeNull());
    await act(async () => {
      FakeEventSource.latest?.emit("snapshot", {
        ...initial,
        connection: { state: "live", queueDepth: 0 },
        interfaces: [{ port: "Gi0/6", description: "", status: "notconnect", admin_state: "up", oper_state: "down", speed: "auto", duplex: "auto", vlan: "1", poe_state: "off", poe_watts: 0, protected: false }],
      } satisfies LiveSnapshot);
    });
    expect(screen.getByTestId("connection").textContent).toBe("live");
    expect(screen.getByTestId("port").textContent).toBe("Gi0/6");
  });
});
