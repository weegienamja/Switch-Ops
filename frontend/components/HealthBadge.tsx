"use client";
import { motion, useReducedMotion } from "motion/react";

type State = "healthy" | "warning" | "critical" | "unknown";

const COPY: Record<State, string> = {
  healthy: "Healthy",
  warning: "Warning",
  critical: "Critical",
  unknown: "Unknown",
};

export default function HealthBadge({
  state,
  label,
}: {
  state: State;
  label?: string;
}) {
  const reduce = useReducedMotion();
  const pulse = state === "healthy" && !reduce;
  return (
    <span className={`hb hb-${state}`} role="status" aria-label={label ?? COPY[state]}>
      <motion.span
        className="dot"
        animate={pulse ? { opacity: [0.4, 1, 0.4] } : undefined}
        transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
      />
      <span className="text">{label ?? COPY[state]}</span>
      <style jsx>{`
        .hb {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
          font-weight: 600;
          letter-spacing: 0.04em;
          text-transform: uppercase;
          border: 1px solid rgba(148, 163, 184, 0.18);
          background: rgba(15, 23, 36, 0.6);
        }
        .dot {
          width: 8px;
          height: 8px;
          border-radius: 999px;
          background: currentColor;
          box-shadow: 0 0 10px currentColor;
          display: inline-block;
        }
        .hb-healthy {
          color: #22c55e;
        }
        .hb-warning {
          color: #f59e0b;
        }
        .hb-critical {
          color: #ef4444;
        }
        .hb-unknown {
          color: #94a3b8;
        }
        .text {
          color: #e2e8f0;
        }
      `}</style>
    </span>
  );
}
