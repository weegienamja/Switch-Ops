"use client";
import type { InterfaceStatus } from "@/lib/types";

const PORTS = [
  "Gi0/1",
  "Gi0/2",
  "Gi0/3",
  "Gi0/4",
  "Gi0/5",
  "Gi0/6",
  "Gi0/7",
  "Gi0/8",
  "Gi0/9",
  "Gi0/10",
];

export default function PortTopologyMap({
  interfaces,
}: {
  interfaces: InterfaceStatus[];
}) {
  const byPort = new Map(interfaces.map((i) => [i.port, i]));
  return (
    <div className="card">
      <div className="card__head">
        <div>
          <h3 className="card__title">Front-panel topology</h3>
          <div className="card__subtitle">
            Gi0/1–Gi0/2 are protected uplinks. Gi0/3–Gi0/8 are lab access ports.
          </div>
        </div>
      </div>
      <div className="topology">
        {PORTS.map((p) => {
          const i = byPort.get(p);
          const connected = i?.status === "connected";
          const shutdown = i?.notes?.toLowerCase().includes("shutdown") || i?.status === "disabled";
          const protectedPort = i?.protected || p === "Gi0/1" || p === "Gi0/2";
          const cls = [
            "port",
            connected ? "port--connected" : "",
            shutdown ? "port--shutdown" : "",
            protectedPort ? "port--protected" : "",
          ]
            .filter(Boolean)
            .join(" ");
          return (
            <div key={p} className={cls} title={i?.name || p}>
              {protectedPort && <span className="port__lock">LOCK</span>}
              <span className="port__num">{p.replace("Gi0/", "")}</span>
              <span className="port__name">
                {connected ? "up" : shutdown ? "shut" : i?.status || "—"}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
