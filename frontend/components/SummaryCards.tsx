"use client";
import { motion } from "motion/react";
import type { SwitchSummary, InterfaceErrorsResponse, PoeResponse } from "@/lib/types";

const stagger = {
  hidden: {},
  show: { transition: { staggerChildren: 0.05 } },
};

interface Props {
  summary: SwitchSummary;
  errors: InterfaceErrorsResponse;
  poe: PoeResponse;
}

/** Quick-glance metric tiles. Used at the top of the dashboard. */
export default function SummaryCards({ summary, errors, poe }: Props) {
  const cards = [
    {
      label: "Temperature",
      value: summary.temperatureC != null ? `${summary.temperatureC}°C` : "—",
      tone: summary.temperatureState?.toLowerCase() ?? "unknown",
    },
    {
      label: "CPU",
      value: summary.cpu5Sec != null ? `${summary.cpu5Sec.toFixed(0)}%` : "—",
      tone: (summary.cpu5Sec ?? 0) > 80 ? "critical" : "healthy",
    },
    {
      label: "PoE used",
      value: `${poe.usedWatts.toFixed(0)} / ${poe.availableWatts.toFixed(0)} W`,
      tone: "healthy",
    },
    {
      label: "Interface errors",
      value: `${errors.totalErrors}`,
      tone: errors.healthy ? "healthy" : "critical",
    },
    {
      label: "Connected",
      value: `${summary.connectedPorts.length} ports`,
      tone: "healthy",
    },
    {
      label: "Shutdown",
      value: `${summary.shutdownPorts.length} ports`,
      tone: "unknown",
    },
  ];

  return (
    <motion.section
      className="summary-grid"
      initial="hidden"
      animate="show"
      variants={stagger}
      aria-label="Summary metrics"
    >
      {cards.map((c) => (
        <motion.div
          key={c.label}
          className={`summary-tile tone-${c.tone}`}
          variants={{
            hidden: { opacity: 0, y: 12 },
            show: { opacity: 1, y: 0, transition: { duration: 0.35, ease: "easeOut" } },
          }}
        >
          <div className="summary-label">{c.label}</div>
          <div className="summary-value">{c.value}</div>
        </motion.div>
      ))}
      <style jsx global>{`
        .summary-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
          gap: 14px;
          margin-bottom: 8px;
        }
        .summary-tile {
          border: 1px solid rgba(148, 163, 184, 0.14);
          background: rgba(15, 23, 36, 0.65);
          backdrop-filter: blur(14px);
          border-radius: 12px;
          padding: 14px 16px;
        }
        .summary-label {
          color: #94a3b8;
          font-size: 11px;
          text-transform: uppercase;
          letter-spacing: 0.08em;
          margin-bottom: 6px;
        }
        .summary-value {
          color: #e2e8f0;
          font-size: 22px;
          font-weight: 600;
          font-variant-numeric: tabular-nums;
        }
        .tone-green .summary-value,
        .tone-healthy .summary-value {
          color: #22c55e;
        }
        .tone-yellow .summary-value,
        .tone-warning .summary-value {
          color: #f59e0b;
        }
        .tone-red .summary-value,
        .tone-critical .summary-value {
          color: #ef4444;
        }
      `}</style>
    </motion.section>
  );
}
