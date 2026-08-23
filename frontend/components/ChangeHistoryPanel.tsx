"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ChangeSession } from "@/lib/types";

function formatStatus(value: string): string {
  return value.replaceAll("_", " ").toUpperCase();
}

export default function ChangeHistoryPanel({ active }: { active?: ChangeSession | null }) {
  const [sessions, setSessions] = useState<ChangeSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void api.changeSessions(30).then(
      (response) => {
        if (cancelled) return;
        setSessions(response.sessions);
        setError(null);
        setLoading(false);
      },
      (cause) => {
        if (cancelled) return;
        setError(cause instanceof Error ? cause.message : String(cause));
        setLoading(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [active?.id, active?.status, active?.updatedAt]);

  return (
    <section className="card change-history" aria-labelledby="change-history-title">
      <div className="card__head">
        <div>
          <div className="eyebrow">Durable local record</div>
          <h2 className="card__title" id="change-history-title">Change history</h2>
          <div className="card__subtitle">
            Reopen the evidence, outcome and running/startup state from prior bounded sessions.
          </div>
        </div>
        <span className="badge">{sessions.length} SESSIONS</span>
      </div>
      {loading ? <p className="change-history__empty">Loading local change history…</p> : null}
      {error ? <p className="change-history__error" role="alert">{error}</p> : null}
      {!loading && !error && sessions.length === 0 ? (
        <p className="change-history__empty">
          No Change Assurance sessions yet. Creating a plan records it even if preflight blocks execution.
        </p>
      ) : null}
      <div className="change-history__list">
        {sessions.map((session) => (
          <details key={session.id} className={`change-history__item change-history__item--${session.status}`}>
            <summary>
              <span className="change-history__mark" aria-hidden />
              <span>
                <strong>{session.plan.declaredIntent.summary}</strong>
                <small>{new Date(session.updatedAt).toLocaleString()} · {session.plan.targetInterface}</small>
              </span>
              <b>{formatStatus(session.status)}</b>
            </summary>
            <div className="change-history__body">
              <p>{session.outcomeDetail}</p>
              <dl>
                <div><dt>Plan</dt><dd>{session.plan.id}</dd></div>
                <div><dt>Operation</dt><dd>{session.plan.steps[0]?.kind || "unknown"}</dd></div>
                <div><dt>Preflight</dt><dd>{session.preflight?.outcome || "not run"}</dd></div>
                <div><dt>Postcondition</dt><dd>{session.comparison?.directPostcondition || "not observed"}</dd></div>
              </dl>
              {session.beforeSnapshot && session.afterSnapshot ? (
                <div className="change-history__comparison">
                  <span>Before: {session.beforeSnapshot.target.adminState} / {session.beforeSnapshot.target.operState}</span>
                  <span>After: {session.afterSnapshot.target.adminState} / {session.afterSnapshot.target.operState}</span>
                  <span>
                    Startup config: {session.beforeSnapshot.configuration.startupFingerprint === session.afterSnapshot.configuration.startupFingerprint ? "unchanged" : "changed"}
                  </span>
                </div>
              ) : null}
              {session.comparison?.warnings.length ? (
                <ul>
                  {session.comparison.warnings.map((warning) => <li key={warning}>{warning}</li>)}
                </ul>
              ) : null}
            </div>
          </details>
        ))}
      </div>
    </section>
  );
}
