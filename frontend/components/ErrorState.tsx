"use client";

import type {
  ApiError,
  ApiErrorCategory,
  ManagementPathAssurance,
} from "@/lib/api";
import ManagementPathReview from "./ManagementPathReview";

const PRESENTATION: Record<ApiErrorCategory, { title: string; explanation: string }> = {
  BACKEND_UNREACHABLE: {
    title: "Backend unavailable",
    explanation: "SwitchOps could not reach its local backend sidecar.",
  },
  DEVICE_UNREACHABLE: {
    title: "Catalyst unavailable",
    explanation:
      "SwitchOps is running, but the configured Catalyst could not be reached. The PC's network connection may have changed, or the device may be offline.",
  },
  DEVICE_AUTH_FAILED: {
    title: "Catalyst authentication failed",
    explanation: "The Catalyst was reached, but it rejected the stored SSH credentials.",
  },
  DEVICE_HOST_KEY_CHANGED: {
    title: "Catalyst host key changed",
    explanation:
      "SwitchOps refused the SSH connection because the device key no longer matches the pinned key. Review this change deliberately.",
  },
  DEVICE_SSH_NEGOTIATION_FAILED: {
    title: "Catalyst SSH negotiation failed",
    explanation: "The backend reached the device but could not complete a compatible SSH handshake.",
  },
  DEVICE_SESSION_LOST: {
    title: "Catalyst session lost",
    explanation:
      "The active SSH transport stopped working. SwitchOps discarded that session and will reconnect cleanly.",
  },
  BACKEND_INTERNAL_ERROR: {
    title: "SwitchOps backend error",
    explanation: "The backend is running, but it could not complete this request.",
  },
};

function observationLabel(value?: string | null): string {
  if (!value) return "No successful observation yet";
  const observedAt = new Date(value);
  return Number.isNaN(observedAt.getTime()) ? value : observedAt.toLocaleString();
}

export default function ErrorState({
  error,
  onRetry,
  deviceLabel = "Configured Catalyst",
  lastSuccessfulObservation,
  sessionState,
  managementPath,
}: {
  error: ApiError;
  onRetry?: () => void;
  deviceLabel?: string;
  lastSuccessfulObservation?: string | null;
  sessionState?: string | null;
  managementPath?: ManagementPathAssurance | null;
}) {
  const presentation = PRESENTATION[error.category];
  const deviceFailure = error.category.startsWith("DEVICE_");
  const showSidecarHelp = error.category === "BACKEND_UNREACHABLE" && !error.backendResponded;
  const pathDiagnosis = deviceFailure ? managementPath?.diagnosis : undefined;
  const pathSpecific = pathDiagnosis && [
    "HOST_NETWORK_CHANGED",
    "HOST_ROUTE_MISSING",
    "HOST_PATH_DEGRADED",
  ].includes(pathDiagnosis.conclusion);

  return (
    <div className="app-shell">
      <div className="container">
        <div className="card" role="alert" style={{ borderColor: "rgba(239,68,68,0.35)" }}>
          <h3 className="card__title" style={{ color: "var(--red)" }}>
            {pathSpecific ? pathDiagnosis.headline : presentation.title}
          </h3>
          <p style={{ color: "var(--text-muted)", lineHeight: 1.6 }}>
            {pathSpecific ? pathDiagnosis.summary : presentation.explanation}
          </p>
          <div className="mono" style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 16 }}>
            <div>{error.message}</div>
            {error.detail ? <div>{error.detail}</div> : null}
            <div>
              {error.status === null ? "No HTTP response" : `HTTP ${error.status}`} · {error.code}
            </div>
            {deviceFailure ? (
              <>
                <div>Configured device: {deviceLabel}</div>
                <div>Last successful observation: {observationLabel(lastSuccessfulObservation)}</div>
                <div>Session state: {sessionState || "offline"}</div>
              </>
            ) : null}
          </div>
          {managementPath && deviceFailure ? (
            <div className="management-path-assurance">
              <div>
                <strong>Last known good</strong>
                {managementPath.lastKnownGood ? (
                  <>
                    <span>
                      {managementPath.lastKnownGood.adapterName || "Host adapter"}: {managementPath.lastKnownGood.sourceIp || "unknown source"}
                      {managementPath.lastKnownGood.prefixLength == null ? "" : `/${managementPath.lastKnownGood.prefixLength}`}
                      {managementPath.lastKnownGood.catalystInterface ? ` · Catalyst ${managementPath.lastKnownGood.catalystInterface}` : ""}
                      {managementPath.lastKnownGood.managementPrefix ? ` · Catalyst management prefix ${managementPath.lastKnownGood.managementPrefix}` : ""}
                    </span>
                    <span className={`evidence-freshness evidence-freshness--${managementPath.lastKnownGood.freshness}`}>
                      Historical evidence · {managementPath.lastKnownGood.freshness} · {observationLabel(managementPath.lastKnownGood.observedAt)}
                    </span>
                  </>
                ) : <span>No complete historical management path is available.</span>}
              </div>
              <div>
                <strong>Current Windows path</strong>
                <span>
                  {managementPath.current.adapterName || "Selected adapter"}: {managementPath.current.sourceIp || "unknown source"}
                  {managementPath.current.prefixLength == null ? "" : `/${managementPath.current.prefixLength}`}
                  {managementPath.current.route.nextHop ? ` · via ${managementPath.current.route.nextHop}` : ""}
                </span>
                <span>
                  Windows connectivity: {managementPath.current.windowsConnectivity || "unknown"} · Catalyst TCP/22: {managementPath.current.tcp22.replaceAll("_", " ")}
                </span>
              </div>
              <div>
                <strong>Assessment · {managementPath.diagnosis.confidence} confidence</strong>
                <span>Current Catalyst health remains unverified while the management path is unavailable.</span>
              </div>
              <details>
                <summary>Review management-path evidence</summary>
                <p className="mono management-path-conclusion">
                  Diagnosis: {managementPath.diagnosis.conclusion} · Confidence:{" "}
                  {managementPath.diagnosis.confidence}
                </p>
                <ul>
                  {managementPath.diagnosis.evidence.map((item) => <li key={item}>{item}</li>)}
                  {managementPath.diagnosis.missingEvidence.map((item) => <li key={`missing-${item}`}>Missing: {item}</li>)}
                </ul>
                <p>This assessment separates current observation, durable history, and inference.</p>
              </details>
              <ManagementPathReview assurance={managementPath} />
            </div>
          ) : null}
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            {onRetry ? (
              <button type="button" className="btn btn--primary" onClick={onRetry}>
                {deviceFailure ? "Retry connection" : "Retry"}
              </button>
            ) : null}
            {showSidecarHelp ? (
              <div style={{ color: "var(--text-dim)", fontSize: 12 }}>
                Ensure the backend sidecar is running on 127.0.0.1:8765.
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
