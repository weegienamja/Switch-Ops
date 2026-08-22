"use client";

import { useMemo, useState } from "react";
import type { NetworkDevice, NetworkEvent } from "@/lib/types";

/** Events shown before the reader has to ask for the rest. */
const VISIBLE_EVENTS = 25;

export default function NetworkEventTimeline({
  events,
  devices,
  initialInterface = "all",
}: {
  events: NetworkEvent[];
  devices: NetworkDevice[];
  initialInterface?: string;
}) {
  const [interfaceFilter, setInterfaceFilter] = useState(initialInterface);
  const [severity, setSeverity] = useState("all");
  const [eventType, setEventType] = useState("all");
  const [deviceId, setDeviceId] = useState("all");
  // A busy uplink can accumulate a long tail of address churn. Show a
  // readable window by default and let the reader ask for the rest.
  const [showAll, setShowAll] = useState(false);
  const interfaces = useMemo(
    () => Array.from(new Set(events.map((event) => event.interface).filter(Boolean) as string[])).sort(),
    [events],
  );
  const eventTypes = useMemo(
    () => Array.from(new Set(events.map((event) => event.eventType))).sort(),
    [events],
  );
  const filtered = events.filter((event) => (
    (interfaceFilter === "all" || event.interface === interfaceFilter)
    && (severity === "all" || event.severity === severity)
    && (eventType === "all" || event.eventType === eventType)
    && (deviceId === "all" || event.deviceId === deviceId)
  ));
  const visible = showAll ? filtered : filtered.slice(0, VISIBLE_EVENTS);
  const hidden = filtered.length - visible.length;

  return (
    <section className="card event-timeline" aria-labelledby="network-events-title">
      <div className="card__head">
        <div>
          <h2 className="card__title" id="network-events-title">Network events</h2>
          <div className="card__subtitle">
            Meaningful observed transitions, separate from the command audit trail.
          </div>
        </div>
        <span className="badge">
          {visible.length === filtered.length
            ? `${filtered.length} shown`
            : `${visible.length} of ${filtered.length}`}
        </span>
      </div>
      <div className="event-filters" aria-label="Event filters">
        <label>
          <span>Interface</span>
          <select value={interfaceFilter} onChange={(event) => setInterfaceFilter(event.target.value)}>
            <option value="all">All interfaces</option>
            {interfaces.map((port) => <option key={port} value={port}>{port}</option>)}
          </select>
        </label>
        <label>
          <span>Severity</span>
          <select value={severity} onChange={(event) => setSeverity(event.target.value)}>
            <option value="all">All severities</option>
            <option value="HEALTHY">Healthy</option>
            <option value="NOTICE">Notice</option>
            <option value="ATTENTION">Attention</option>
            <option value="CRITICAL">Critical</option>
          </select>
        </label>
        <label>
          <span>Event type</span>
          <select value={eventType} onChange={(event) => setEventType(event.target.value)}>
            <option value="all">All event types</option>
            {eventTypes.map((type) => (
              <option key={type} value={type}>{type.replaceAll("_", " ")}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Device</span>
          <select value={deviceId} onChange={(event) => setDeviceId(event.target.value)}>
            <option value="all">All devices</option>
            {devices.map((device) => <option key={device.id} value={device.id}>{device.name}</option>)}
          </select>
        </label>
      </div>

      {filtered.length ? (
        <ol className="network-events-list">
          {visible.map((event, index) => (
            <li key={event.id ?? `${event.timestamp}-${index}`}>
              <div className={`event-severity event-severity--${event.severity.toLowerCase()}`}>
                <span aria-hidden />
                <small>{event.severity}</small>
              </div>
              <time dateTime={event.timestamp}>
                {new Date(event.timestamp).toLocaleDateString([], { month: "short", day: "numeric" })}
                <strong>{new Date(event.timestamp).toLocaleTimeString()}</strong>
              </time>
              <div className="network-event-copy">
                <strong>{event.title}</strong>
                <p>{event.detail}</p>
                <div>
                  {event.interface ? <span className="badge">{event.interface}</span> : null}
                  <span className="badge">{event.eventType.replaceAll("_", " ")}</span>
                </div>
              </div>
            </li>
          ))}
          {hidden > 0 ? (
            <li className="event-timeline__more">
              <button type="button" className="btn btn--ghost" onClick={() => setShowAll(true)}>
                Show {hidden} older event{hidden === 1 ? "" : "s"}
              </button>
            </li>
          ) : null}
        </ol>
      ) : (
        <div className="event-empty">
          <span className="event-empty__line" aria-hidden />
          <strong>No matching network events</strong>
          <p>The first observation establishes a baseline. Future refreshes record changes, not repetitive “still the same” entries.</p>
        </div>
      )}
    </section>
  );
}
