import type { SetupStatus } from "@/lib/types";

export default function RuntimeBadge({ setup }: { setup: Pick<SetupStatus, "mockMode"> }) {
  return (
    <span className={`badge ${setup.mockMode ? "badge--cyan" : "badge--green"}`}>
      <span className="dot" />
      {setup.mockMode ? "mock simulation" : "real device"}
    </span>
  );
}
