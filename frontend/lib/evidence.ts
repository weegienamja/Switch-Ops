import type {
  DeviceType,
  EvidenceLevel,
  IdentitySource,
  NetworkDevice,
  NetworkLink,
} from "./types";

/**
 * Presentation vocabulary for the topology evidence model.
 *
 * Every label here has to survive the question "how does SwitchOps know?".
 * The rule is that existence claims and identity claims are worded separately:
 * a link can be certain while the thing on the end of it is a guess taken from
 * an interface description.
 */

/** Human names for device categories. Never shown raw from the model. */
export const DEVICE_TYPE_LABELS: Record<DeviceType, string> = {
  router: "Router",
  switch: "Switch",
  "access-point": "Access point",
  desktop: "Desktop",
  laptop: "Laptop",
  server: "Server",
  phone: "Phone",
  "tv-media": "TV / media",
  printer: "Printer",
  camera: "Camera",
  unknown: "Unknown device",
};

export interface EvidenceCopy {
  /** Two or three words for a chip. */
  label: string;
  /** One sentence a beginner can act on. */
  detail: string;
}

export const EVIDENCE_COPY: Record<EvidenceLevel, EvidenceCopy> = {
  direct: {
    label: "Direct neighbour",
    detail:
      "The device on this port announced itself to the switch over CDP, so it is directly attached.",
  },
  "observed-on-port": {
    label: "Observed on port",
    detail:
      "The switch has a live link on this port and is learning addresses through it, so something is attached. SwitchOps cannot prove it is a single device rather than another switch.",
  },
  "learned-behind": {
    label: "Learned behind",
    detail:
      "This address was learned through the interface. It may belong to a device several hops away, not to the device on the port.",
  },
  expected: {
    label: "Expected",
    detail:
      "Only the interface description suggests this device. Nothing has been observed on the port yet.",
  },
  unknown: {
    label: "Unknown",
    detail: "There is not enough evidence to say what is on this interface.",
  },
};

export const IDENTITY_COPY: Record<IdentitySource, string> = {
  cdp: "Name and platform reported by the device itself (CDP).",
  lldp: "Name and platform reported by the device itself (LLDP).",
  "local-host": "Matched to this SwitchOps PC through its active local adapter and the switch MAC table.",
  "interface-description":
    "Name taken from the interface description you configured, not from the device.",
  "mac-oui": "Vendor inferred from the hardware address prefix.",
  "user-intent": "Name you recorded in SwitchOps. It is an expectation, not a sighting.",
  "meraki-api": "Reported by a Meraki controller.",
  historical: "Carried over from an earlier observation; it may no longer hold.",
  "switch-telemetry": "Read from this switch over an authenticated session.",
  none: "Nothing identifies this device yet.",
};

/** Short status word shown on a topology node. Never colour-only. */
export function deviceStateLabel(device: NetworkDevice): string {
  if (device.source === "expected") return "WAITING";
  if (device.online) return "LINK UP";
  return "NO LINK";
}

export function deviceStateTone(
  device: NetworkDevice,
): "up" | "waiting" | "down" {
  if (device.source === "expected") return "waiting";
  return device.online ? "up" : "down";
}

/**
 * What to show under the device name. Prefers a proven identity, degrades to
 * the category, and says so when nothing is known.
 */
export function deviceIdentityLine(device: NetworkDevice): string {
  if (device.model && device.vendor) return `${device.vendor} ${device.model}`;
  if (device.model) return device.model;
  if (device.vendor) return device.vendor;
  return DEVICE_TYPE_LABELS[device.type] || "Not identified";
}

/** "2 observed · 3 expected" — never "5 connected". */
export function topologyCountLabel(devices: NetworkDevice[]): string {
  const observed = devices.filter((device) => device.source === "observed").length;
  const expected = devices.filter((device) => device.source === "expected").length;
  const parts: string[] = [];
  if (observed) parts.push(`${observed} observed`);
  if (expected) parts.push(`${expected} expected`);
  return parts.join(" · ") || "nothing evidenced";
}

/**
 * The honest sentence about addresses reachable through a link. Returns null
 * when there is nothing worth saying.
 */
export function learnedBehindNote(count: number): string | null {
  if (count <= 1) return null;
  return `${count} addresses are reachable through this link. They sit behind the device on this port, not on the port itself.`;
}

export function learnedBehindChip(count: number): string | null {
  if (count <= 1) return null;
  return `+${count - 1} behind`;
}

/** Dash pattern intent for a link. Solid = observed, dashed = inferred. */
export function linkStyle(link: NetworkLink | undefined): "solid" | "dashed" | "idle" {
  if (!link) return "idle";
  if (link.evidenceLevel === "expected") return "dashed";
  if (link.status === "up") return "solid";
  return "idle";
}

export function confidenceLabel(confidence: "low" | "medium" | "high"): string {
  return `${confidence} confidence`;
}
