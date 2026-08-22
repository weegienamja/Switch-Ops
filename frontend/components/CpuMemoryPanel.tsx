"use client";
import type { CpuStatus, MemoryStatus } from "@/lib/types";

function pct(used?: number | null, total?: number | null): number {
  if (!used || !total) return 0;
  return Math.min(100, (used / total) * 100);
}

export default function CpuMemoryPanel({
  cpu,
  memory,
}: {
  cpu: CpuStatus;
  memory: MemoryStatus;
}) {
  const memPct = pct(memory.processorUsed, memory.processorTotal);
  const cpuPct = cpu.cpu5Sec ?? 0;
  return (
    <div className="card">
      <div className="card__head">
        <h3 className="card__title">CPU &amp; Memory</h3>
      </div>
      <div className="stat" style={{ marginBottom: 14 }}>
        <div className="stat__label">CPU (5s)</div>
        <div className="stat__value">
          {cpu.cpu5Sec ?? "—"}
          <span className="stat__suffix">%</span>
        </div>
        <div className="stat__sub">
          1m {cpu.cpu1Min ?? "—"}% · 5m {cpu.cpu5Min ?? "—"}%
        </div>
      </div>
      <div className={`bar ${cpuPct > 80 ? "bar--amber" : ""}`} style={{ marginBottom: 18 }}>
        <div className="bar__fill" style={{ ["--pct" as any]: `${cpuPct}%` }} />
      </div>

      <div className="stat" style={{ marginBottom: 14 }}>
        <div className="stat__label">Memory (processor)</div>
        <div className="stat__value">
          {memPct.toFixed(0)}
          <span className="stat__suffix">%</span>
        </div>
        <div className="stat__sub">
          {memory.processorUsed ?? "—"} / {memory.processorTotal ?? "—"} bytes
        </div>
      </div>
      <div className={`bar ${memPct > 85 ? "bar--amber" : ""}`}>
        <div className="bar__fill" style={{ ["--pct" as any]: `${memPct}%` }} />
      </div>
    </div>
  );
}
