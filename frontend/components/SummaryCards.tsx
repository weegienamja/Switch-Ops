"use client";

import { motion } from "motion/react";
import type { PoeResponse, SwitchSummary, TelemetrySnapshotSummary } from "@/lib/types";

const stagger = {
  hidden: {},
  show: { transition: { staggerChildren: 0.05 } },
};

export default function SummaryCards({
  summary,
  poe,
  telemetry,
}: {
  summary: SwitchSummary;
  poe: PoeResponse;
  telemetry: TelemetrySnapshotSummary;
}) {
  const newErrors = telemetry.interfaceDeltas.reduce(
    (total, delta) => total + Math.max(0, delta.errorDelta || 0),
    0,
  );
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
      label: "New errors",
      value: telemetry.historyAvailable ? `+${newErrors}` : "baseline",
      tone: newErrors > 0 ? "warning" : "healthy",
    },
    {
      label: "Connected",
      value: `${summary.connectedPorts.length} ports`,
      tone: "healthy",
    },
    {
      label: "Disabled",
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
      {cards.map((card) => (
        <motion.div
          key={card.label}
          className={`summary-tile tone-${card.tone}`}
          variants={{
            hidden: { opacity: 0, y: 12 },
            show: { opacity: 1, y: 0, transition: { duration: 0.35, ease: "easeOut" } },
          }}
        >
          <div className="summary-label">{card.label}</div>
          <div className="summary-value">{card.value}</div>
        </motion.div>
      ))}
    </motion.section>
  );
}
