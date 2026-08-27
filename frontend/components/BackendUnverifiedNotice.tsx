"use client";

import {
  describeBackendUnverified,
  type BackendUnverified,
} from "@/lib/backendIntegrity";

/**
 * Terminal state for a backend this desktop session cannot vouch for.
 *
 * There is deliberately no retry control. Retrying would only re-query the
 * process the shell already rejected; the operator has to stop that process
 * and start SwitchOps again.
 */
export default function BackendUnverifiedNotice({
  state,
}: {
  state: BackendUnverified;
}) {
  const { reason, observed } = state;

  return (
    <div className="app-shell">
      <div className="container">
        <div
          className="card backend-unverified"
          role="alert"
          aria-labelledby="backend-unverified-title"
        >
          <h3
            id="backend-unverified-title"
            className="card__title"
            style={{ color: "var(--red)" }}
          >
            Backend verification failed
          </h3>
          <p style={{ color: "var(--text-muted)", lineHeight: 1.6 }}>
            SwitchOps detected a local backend process that does not belong to
            this desktop session. The dashboard has not used that process, and
            no device or network information below can be trusted from it.
          </p>

          <div className="backend-unverified__facts">
            <div>
              <strong>Reason</strong>
              <span className="mono">{reason}</span>
            </div>
            <div>
              <strong>What this means</strong>
              <span>{describeBackendUnverified(reason)}</span>
            </div>
            {observed ? (
              <div>
                <strong>Backend that answered</strong>
                <span className="mono">
                  build {observed.buildId} · {observed.runtimeMode}
                  {observed.apiSchemaVersion
                    ? ` · API schema v${observed.apiSchemaVersion}`
                    : ""}
                </span>
              </div>
            ) : null}
            <div>
              <strong>Action</strong>
              <span>
                Stop the conflicting SwitchOps or development backend, then
                start SwitchOps again.
              </span>
            </div>
          </div>

          <p className="backend-unverified__scope">
            This is a local runtime problem. It is not a Catalyst fault, not a
            management-path fault, and says nothing about whether the device is
            reachable.
          </p>
        </div>
      </div>
    </div>
  );
}
