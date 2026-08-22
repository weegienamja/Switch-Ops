import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import RuntimeBadge from "@/components/RuntimeBadge";

describe("runtime indicator", () => {
  it("labels synthetic data as a mock simulation", () => {
    render(<RuntimeBadge setup={{ mockMode: true }} />);
    expect(screen.getByText("mock simulation")).toBeTruthy();
  });

  it("labels physical telemetry as real device data", () => {
    render(<RuntimeBadge setup={{ mockMode: false }} />);
    expect(screen.getByText("real device")).toBeTruthy();
  });
});
