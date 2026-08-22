export function formatBytes(n?: number | null): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

export function formatPercent(n?: number | null): string {
  if (n == null) return "—";
  return `${n.toFixed(0)}%`;
}

export function formatWatts(n: number | undefined | null): string {
  if (n == null) return "—";
  return `${n.toFixed(1)} W`;
}

export function timeAgo(iso: string | Date): string {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  const diff = Math.max(0, Date.now() - d.getTime());
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const days = Math.floor(h / 24);
  return `${days}d ago`;
}

export function statusBadgeClass(state: string): string {
  switch (state) {
    case "GREEN":
      return "badge--green";
    case "YELLOW":
      return "badge--amber";
    case "RED":
      return "badge--red";
    default:
      return "";
  }
}
