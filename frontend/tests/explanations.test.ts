import { describe, expect, it } from "vitest";
import {
  explainInterfaceStatus,
  explainLink,
  explainPoe,
  explainVlan,
} from "@/lib/explanations";

describe("beginner explanations", () => {
  it("distinguishes disabled from notconnect", () => {
    expect(explainInterfaceStatus("disabled")).toContain("administratively shut down");
    expect(explainInterfaceStatus("notconnect")).toContain("enabled");
    expect(explainInterfaceStatus("notconnect")).toContain("does not currently detect");
  });

  it("explains a 1 Gbps full-duplex link", () => {
    const explanation = explainLink("a-1000", "a-full");
    expect(explanation).toContain("1 gigabit per second");
    expect(explanation).toContain("simultaneously");
  });

  it("explains VLAN and PoE state without guessing", () => {
    expect(explainVlan("1")).toContain("VLAN 1");
    expect(explainPoe("off")).toContain("not currently supplying power");
  });
});
