/**
 * Local runtime integrity: is the backend on loopback the one this desktop
 * session started?
 *
 * This is deliberately separate from every network and device diagnosis. An
 * unverified backend is not a Catalyst problem, not a management-path problem,
 * and not "device offline" — it means the dashboard has no trustworthy data
 * source at all, and must not query or believe the process on the port.
 */

import { isTauriDesktop } from "./ewpsDesktop";

export const BACKEND_UNVERIFIED_EVENT = "switchops://backend-unverified";

export type BackendUnverifiedReason =
  /** Another process already owned 8765, so no sidecar was started. */
  | "PORT_ALREADY_BOUND"
  /** A backend answered but predates the provenance endpoint. */
  | "BACKEND_TOO_OLD"
  /** A backend identified itself but is not this launch's sidecar. */
  | "FOREIGN_BACKEND"
  /** Nothing answered before the deadline. */
  | "NO_RESPONSE";

/** Safe self-reported facts about a backend that is not ours. */
export interface ObservedBackend {
  buildId: string;
  apiSchemaVersion: number;
  runtimeMode: string;
  startedAt: string;
}

export interface BackendUnverified {
  reason: BackendUnverifiedReason;
  observed?: ObservedBackend | null;
}

const REASON_TEXT: Record<BackendUnverifiedReason, string> = {
  PORT_ALREADY_BOUND:
    "Another process was already using the local SwitchOps port, so this session did not start its own backend.",
  BACKEND_TOO_OLD:
    "A SwitchOps backend answered, but it is too old to identify itself and cannot be trusted by this session.",
  FOREIGN_BACKEND:
    "A SwitchOps backend answered, but it was not started by this desktop session.",
  NO_RESPONSE:
    "This session's backend did not start, so the dashboard has no data source.",
};

export function describeBackendUnverified(reason: BackendUnverifiedReason): string {
  return REASON_TEXT[reason] ?? REASON_TEXT.NO_RESPONSE;
}

const REASONS: BackendUnverifiedReason[] = [
  "PORT_ALREADY_BOUND",
  "BACKEND_TOO_OLD",
  "FOREIGN_BACKEND",
  "NO_RESPONSE",
];

/**
 * Accept only the typed shape the shell emits.
 *
 * An unrecognised payload is treated as a verification failure rather than
 * ignored: failing open here would restore exactly the silent fallback this
 * mechanism exists to prevent.
 */
export function parseBackendUnverified(payload: unknown): BackendUnverified | null {
  if (!payload || typeof payload !== "object") return null;
  const candidate = payload as { reason?: unknown; observed?: unknown };
  const reason = REASONS.includes(candidate.reason as BackendUnverifiedReason)
    ? (candidate.reason as BackendUnverifiedReason)
    : "NO_RESPONSE";

  let observed: ObservedBackend | null = null;
  const raw = candidate.observed;
  if (raw && typeof raw === "object") {
    const item = raw as Record<string, unknown>;
    if (typeof item.buildId === "string" && typeof item.runtimeMode === "string") {
      observed = {
        buildId: item.buildId,
        apiSchemaVersion:
          typeof item.apiSchemaVersion === "number" ? item.apiSchemaVersion : 0,
        runtimeMode: item.runtimeMode,
        startedAt: typeof item.startedAt === "string" ? item.startedAt : "",
      };
    }
  }
  return { reason, observed };
}

/**
 * Ask the shell for its verdict.
 *
 * The event can fire before the webview has registered a listener, so asking
 * is the reliable path: a missed event would leave the dashboard waiting on a
 * backend the shell already rejected. `null` means verified, or that we are
 * not running inside the desktop shell at all.
 */
export async function getBackendVerification(): Promise<BackendUnverified | null> {
  if (!isTauriDesktop()) return null;
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    const result = await invoke<unknown>("backend_verification");
    return result ? parseBackendUnverified(result) : null;
  } catch {
    // An older shell without the command is not evidence either way; let the
    // normal request path report whatever it finds.
    return null;
  }
}

/**
 * Subscribe to the shell's verification verdict.
 *
 * Outside the desktop shell there is nothing to listen to, so this resolves to
 * a no-op unsubscribe rather than throwing.
 */
export async function onBackendUnverified(
  handler: (event: BackendUnverified) => void,
): Promise<() => void> {
  if (!isTauriDesktop()) return () => undefined;
  try {
    const { listen } = await import("@tauri-apps/api/event");
    return await listen(BACKEND_UNVERIFIED_EVENT, (event) => {
      const parsed = parseBackendUnverified(event.payload);
      if (parsed) handler(parsed);
    });
  } catch {
    // A shell without the event API is not evidence of a healthy backend, but
    // it is also not evidence of a broken one. Stay silent and let the normal
    // request path report whatever it finds.
    return () => undefined;
  }
}
