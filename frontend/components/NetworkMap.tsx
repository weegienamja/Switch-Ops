"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type {
  NetworkDevice,
  NetworkLink,
  TelemetrySnapshotSummary,
  TopologyModel,
} from "@/lib/types";
import CatalystFrontPanel from "./CatalystFrontPanel";
import DeviceArt from "./DeviceArt";
import {
  EVIDENCE_COPY,
  deviceIdentityLine,
  deviceStateLabel,
  deviceStateTone,
  learnedBehindChip,
  linkStyle,
  topologyCountLabel,
} from "@/lib/evidence";

interface PlacedDevice {
  device: NetworkDevice;
  link: NetworkLink | undefined;
  port: string;
}

interface Wire {
  id: string;
  path: string;
  tone: "up" | "waiting" | "down";
  style: "solid" | "dashed" | "idle";
  selected: boolean;
  /** Midpoint for the behind-the-link chip. */
  mid: { x: number; y: number } | null;
  behind: string | null;
  label: string | null;
}

function portNumber(port: string): number {
  const match = /Gi0\/(\d+)/.exec(port);
  return match ? Number(match[1]) : 99;
}

function factLine(placed: PlacedDevice): string {
  const { device, link, port } = placed;
  if (device.source === "expected") return `Expected on ${port}`;
  const speed = link?.speed?.toLowerCase().replace("a-", "");
  const speedText =
    speed === "1000" ? "1 Gbps" : speed === "100" ? "100 Mbps" : speed === "10" ? "10 Mbps" : null;
  return [port, speedText, link?.poe ? "PoE" : null].filter(Boolean).join(" · ");
}

/**
 * The physical lab, drawn as one canvas.
 *
 * The Catalyst front panel is the centre of the topology rather than a
 * separate card, and every device is wired to the port it is actually plugged
 * into. Cable geometry is measured from the rendered DOM so a device always
 * lines up with its real port, at any width.
 */
export default function NetworkMap({
  topology,
  telemetry,
  selectedPort,
  onSelectPort,
  model,
}: {
  topology: TopologyModel;
  telemetry: TelemetrySnapshotSummary;
  selectedPort: string;
  onSelectPort: (port: string) => void;
  model?: string;
}) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const nodeRefs = useRef(new Map<string, HTMLElement>());
  const [wires, setWires] = useState<Wire[]>([]);
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });

  const root = topology.devices.find((device) => device.id === topology.rootDeviceId);
  const linkByDevice = new Map(topology.links.map((link) => [link.toDeviceId, link]));

  const placed: PlacedDevice[] = topology.devices
    .filter((device) => device.id !== topology.rootDeviceId)
    .map((device) => {
      const link = linkByDevice.get(device.id);
      return { device, link, port: link?.fromInterface || device.connectedInterface || "" };
    })
    .filter((item) => item.port)
    .sort((a, b) => portNumber(a.port) - portNumber(b.port));

  const upstream = placed.filter((item) => item.device.role === "uplink");
  const edge = placed.filter((item) => item.device.role !== "uplink");

  const registerNode = useCallback((id: string) => (element: HTMLElement | null) => {
    if (element) nodeRefs.current.set(id, element);
    else nodeRefs.current.delete(id);
  }, []);

  // Geometry key: re-measure when the set of drawn things changes.
  const geometryKey = `${placed.map((item) => `${item.device.id}@${item.port}`).join("|")}::${selectedPort}`;

  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    let frame = 0;

    const measure = () => {
      const bounds = canvas.getBoundingClientRect();
      // jsdom and pre-paint frames report zero; draw nothing rather than NaN.
      if (!bounds.width || !bounds.height) {
        setWires([]);
        setCanvasSize({ width: 0, height: 0 });
        return;
      }
      const portElements = new Map<string, Element>();
      canvas.querySelectorAll("[data-port]").forEach((element) => {
        const port = element.getAttribute("data-port");
        if (port) portElements.set(port, element);
      });

      const next: Wire[] = [];
      for (const item of placed) {
        const portElement = portElements.get(item.port);
        const nodeElement = nodeRefs.current.get(item.device.id);
        if (!portElement || !nodeElement) continue;
        const portBox = portElement.getBoundingClientRect();
        const nodeBox = nodeElement.getBoundingClientRect();
        if (!portBox.width || !nodeBox.width) continue;

        const portX = portBox.left + portBox.width / 2 - bounds.left;
        const nodeX = nodeBox.left + nodeBox.width / 2 - bounds.left;
        const above = item.device.role === "uplink";
        const portY = (above ? portBox.top : portBox.bottom) - bounds.top;
        const nodeY = (above ? nodeBox.bottom : nodeBox.top) - bounds.top;
        const span = nodeY - portY;
        const bend = Math.abs(span) * 0.45;
        const path = `M ${portX} ${portY} C ${portX} ${portY + (above ? -bend : bend)}, ${nodeX} ${nodeY + (above ? bend : -bend)}, ${nodeX} ${nodeY}`;

        next.push({
          id: item.device.id,
          path,
          tone: deviceStateTone(item.device),
          style: linkStyle(item.link),
          selected: item.port === selectedPort,
          mid: { x: (portX + nodeX) / 2, y: portY + span / 2 },
          behind: learnedBehindChip(item.link?.learnedMacCount || 0),
          label: item.port === selectedPort ? item.port : null,
        });
      }
      setCanvasSize({ width: bounds.width, height: bounds.height });
      setWires(next);
    };

    const schedule = () => {
      if (typeof cancelAnimationFrame === "function") cancelAnimationFrame(frame);
      frame = typeof requestAnimationFrame === "function"
        ? requestAnimationFrame(measure)
        : (measure(), 0);
    };

    schedule();
    let observer: ResizeObserver | undefined;
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(schedule);
      observer.observe(canvas);
    }
    window.addEventListener("resize", schedule);
    return () => {
      if (typeof cancelAnimationFrame === "function") cancelAnimationFrame(frame);
      observer?.disconnect();
      window.removeEventListener("resize", schedule);
    };
    // placed is derived from topology; geometryKey captures every input that
    // can move a wire.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geometryKey]);

  // Re-measure once web fonts settle, otherwise labels shift after paint.
  useEffect(() => {
    const fonts = (document as Document & { fonts?: { ready?: Promise<unknown> } }).fonts;
    if (!fonts?.ready) return;
    let cancelled = false;
    void fonts.ready.then(() => {
      if (cancelled) return;
      window.dispatchEvent(new Event("resize"));
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className="card network-map" aria-labelledby="network-map-title">
      <div className="card__head">
        <div>
          <h2 className="card__title" id="network-map-title">Your lab</h2>
          <div className="card__subtitle">
            Every device is drawn on the port it is plugged into. Solid cables are observed;
            dashed cables are expected from an interface description only.
          </div>
        </div>
        <span className="badge">{topologyCountLabel(placed.map((item) => item.device))}</span>
      </div>

      <div className="lab-canvas" ref={canvasRef}>
        <svg
          className="lab-canvas__wires"
          aria-hidden
          focusable="false"
          width={canvasSize.width || undefined}
          height={canvasSize.height || undefined}
          viewBox={canvasSize.width ? `0 0 ${canvasSize.width} ${canvasSize.height}` : undefined}
        >
          {wires.map((wire) => (
            <g key={wire.id} className={`wire wire--${wire.tone} wire--${wire.style} ${wire.selected ? "wire--selected" : ""}`}>
              <path className="wire__hit" d={wire.path} />
              <path className="wire__line" d={wire.path} vectorEffect="non-scaling-stroke" />
              {wire.behind && wire.mid ? (
                <g transform={`translate(${wire.mid.x} ${wire.mid.y})`}>
                  <rect className="wire__chip" x="-30" y="-8" width="60" height="16" rx="8" />
                  <text className="wire__chip-text" textAnchor="middle" dy="4">{wire.behind}</text>
                </g>
              ) : null}
            </g>
          ))}
        </svg>

        {upstream.length ? (
          <div className="lab-canvas__tier lab-canvas__tier--upstream">
            <span className="lab-canvas__tier-label">Upstream</span>
            <div className="lab-canvas__row">
              {upstream.map((item) => (
                <TopologyNode
                  key={item.device.id}
                  placed={item}
                  selected={item.port === selectedPort}
                  onSelect={onSelectPort}
                  nodeRef={registerNode(item.device.id)}
                />
              ))}
            </div>
          </div>
        ) : null}

        <div className="lab-canvas__chassis">
          <CatalystFrontPanel
            topology={topology}
            telemetry={telemetry}
            selectedPort={selectedPort}
            onSelectPort={onSelectPort}
            model={model}
          />
          <div className="lab-canvas__chassis-caption">
            <strong>{root?.name || "Managed switch"}</strong>
            <span>{root?.model || "model unknown"}</span>
          </div>
        </div>

        <div className="lab-canvas__tier lab-canvas__tier--edge">
          <span className="lab-canvas__tier-label">Connected &amp; expected</span>
          {edge.length ? (
            <div className="lab-canvas__row">
              {edge.map((item) => (
                <TopologyNode
                  key={item.device.id}
                  placed={item}
                  selected={item.port === selectedPort}
                  onSelect={onSelectPort}
                  nodeRef={registerNode(item.device.id)}
                />
              ))}
            </div>
          ) : (
            <p className="empty-note lab-canvas__empty">
              No endpoint is evidenced yet. Devices appear here once a port has a link, learns an
              address, or carries a description.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

function TopologyNode({
  placed,
  selected,
  onSelect,
  nodeRef,
}: {
  placed: PlacedDevice;
  selected: boolean;
  onSelect: (port: string) => void;
  nodeRef: (element: HTMLElement | null) => void;
}) {
  const { device, port } = placed;
  const tone = deviceStateTone(device);
  const evidence = EVIDENCE_COPY[device.evidenceLevel];
  return (
    <button
      type="button"
      ref={nodeRef}
      data-node={device.id}
      className={`topo-node topo-node--${tone} ${selected ? "topo-node--selected" : ""}`}
      onClick={() => onSelect(port)}
      aria-pressed={selected}
      title={evidence.detail}
    >
      <span className="topo-node__art">
        <DeviceArt type={device.visualCategory} label={device.name} width={82} />
      </span>
      <span className="topo-node__name">{device.name}</span>
      <span className="topo-node__identity">{deviceIdentityLine(device)}</span>
      <span className={`topo-node__state topo-node__state--${tone}`}>
        <i aria-hidden />
        {deviceStateLabel(device)}
      </span>
      <span className="topo-node__fact">{factLine(placed)}</span>
      <span className={`evidence-tag evidence-tag--${device.evidenceLevel}`}>{evidence.label}</span>
    </button>
  );
}
