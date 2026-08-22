import type {
  EvidenceClass,
  EvidenceSource,
  InterfaceReconciliation,
  ReconciliationStatus,
  TopologyAssertion,
} from "./types";

/**
 * Presentation vocabulary for topology reconciliation.
 *
 * Every string here has to survive the question "how does SwitchOps know?".
 * The hard rule is that nothing sourced from an interface description may be
 * worded as though the device was seen, and nothing absent from this switch
 * may be worded as though it were offline.
 */

export interface StatusCopy {
  /** One or two words for a badge. Never colour alone. */
  label: string;
  /** One sentence explaining what the state means. */
  meaning: string;
  /** Visual tone. Paired with the label, never used on its own. */
  tone: "ok" | "warn" | "info" | "muted";
}

export const STATUS_COPY: Record<ReconciliationStatus, StatusCopy> = {
  aligned: {
    label: "Aligned",
    meaning: "What was observed matches what you expect to be here.",
    tone: "ok",
  },
  drift: {
    label: "Drift",
    meaning:
      "Something is here and it is not what you expect. The link itself may be perfectly healthy.",
    tone: "warn",
  },
  "expected-not-observed": {
    label: "Not observed",
    meaning:
      "You expect a device here and this switch cannot see one. That is not the same as the device being offline.",
    tone: "warn",
  },
  unexpected: {
    label: "Unrecorded",
    meaning:
      "Something is attached that no intent accounts for. Recording an expectation lets SwitchOps tell you when it changes.",
    tone: "info",
  },
  uncertain: {
    label: "Unconfirmed",
    meaning:
      "A device is attached, but nothing identifies it, so the expectation can be neither confirmed nor contradicted.",
    tone: "info",
  },
  "not-applicable": {
    label: "Not tracked",
    meaning: "Nothing is expected here and nothing is attached.",
    tone: "muted",
  },
};

/** Statuses that ask the operator for a decision. */
export const NEEDS_ATTENTION: ReconciliationStatus[] = [
  "drift",
  "expected-not-observed",
  "unexpected",
];

export const EVIDENCE_CLASS_COPY: Record<EvidenceClass, StatusCopy> = {
  observed: {
    label: "Observed",
    meaning: "Proven by telemetry read from a device just now.",
    tone: "ok",
  },
  expected: {
    label: "Expected",
    meaning: "What you or the switch's own documentation says should be here. Not a sighting.",
    tone: "info",
  },
  historical: {
    label: "Previously",
    meaning: "What an earlier observation showed. It may no longer be true.",
    tone: "muted",
  },
  inferred: {
    label: "Inferred",
    meaning: "Supported by the evidence but not directly proven.",
    tone: "info",
  },
  unknown: {
    label: "Unknown",
    meaning: "Not enough evidence to say.",
    tone: "muted",
  },
};

/** Where a claim came from, in words a beginner can act on. */
export const SOURCE_COPY: Record<EvidenceSource, string> = {
  cdp: "the device announced itself over CDP",
  lldp: "the device announced itself over LLDP",
  "local-host": "this PC's active adapter matched the switch evidence",
  "mac-table": "addresses learned through the interface",
  arp: "the switch's ARP cache",
  "interface-telemetry": "the interface's own reported state",
  "interface-description": "the description configured on the switch",
  "user-intent": "an expectation you recorded in SwitchOps",
  "accepted-plan": "an accepted change plan",
  "prior-observation": "the previous observation",
  "mac-address-form": "the form of the hardware address itself",
  "meraki-api": "a Meraki controller",
  none: "nothing",
};

/**
 * What to show as the name of whatever is on an interface.
 *
 * Returns the observed identity when something actually identified itself,
 * and otherwise says so. The expected name is returned separately so the UI
 * can present it as the expectation it is.
 */
export function identityLines(result: InterfaceReconciliation): {
  observed: string;
  observedIdentified: boolean;
  expected: string | null;
  expectedSource: EvidenceSource | null;
} {
  const observed = result.observed;
  const expected = result.expected;
  return {
    observed: observed
      ? observed.objectLabel
      : "Nothing attached",
    observedIdentified: Boolean(observed?.objectIdentified),
    expected: expected ? expected.objectLabel : null,
    expectedSource: expected ? expected.source : null,
  };
}

/** Short fact line for a topology node. */
export function reconciliationChip(result: InterfaceReconciliation | undefined): string | null {
  if (!result || result.status === "not-applicable") return null;
  return STATUS_COPY[result.status].label;
}

/**
 * Summary sentence for the Overview card. Deliberately never uses health
 * words: a drifted network can be entirely healthy.
 */
export function summaryTone(
  attention: boolean,
  uncertain: number,
): "ok" | "warn" | "info" {
  if (attention) return "warn";
  if (uncertain) return "info";
  return "ok";
}

/** Group an interface's assertions by what kind of knowledge they are. */
export function assertionsByClass(
  result: InterfaceReconciliation,
): Array<{ evidenceClass: EvidenceClass; assertions: TopologyAssertion[] }> {
  const order: EvidenceClass[] = ["observed", "expected", "inferred", "historical", "unknown"];
  return order
    .map((evidenceClass) => ({
      evidenceClass,
      assertions: result.assertions.filter((item) => item.evidenceClass === evidenceClass),
    }))
    .filter((group) => group.assertions.length > 0);
}
