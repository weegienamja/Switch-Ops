import { DEVICE_TYPE_LABELS } from "@/lib/evidence";
import type { DeviceType } from "@/lib/types";

/**
 * Inline SVG device art.
 *
 * Device art is drawn inline as React SVG rather than loaded as image files so the
 * outline can follow `currentColor`. That is what lets one drawing express
 * "observed", "waiting for evidence" and "not linked" without three copies of
 * every asset, and it keeps the topology free of image requests.
 *
 * Shared visual language:
 *   - chassis fill  : var(--art-fill)
 *   - outline       : currentColor, 2px, round joins
 *   - live accent   : var(--art-accent) (state-driven by the caller)
 */

const FILL = "var(--art-fill)";
const ACCENT = "var(--art-accent)";
const MUTED = "var(--art-muted)";

function Router() {
  return (
    <>
      <path
        d="M31 27 24 11M65 27l7-16"
        stroke={MUTED}
        strokeWidth="2"
        strokeLinecap="round"
      />
      <circle cx="24" cy="10" r="2.5" fill={MUTED} />
      <circle cx="72" cy="10" r="2.5" fill={MUTED} />
      <rect x="12" y="27" width="72" height="24" rx="5" fill={FILL} stroke="currentColor" strokeWidth="2" />
      <path d="M20 45h56" stroke={MUTED} strokeWidth="1.5" strokeDasharray="2 4" opacity=".7" />
      <circle cx="23" cy="36" r="2.6" fill={ACCENT} />
      <circle cx="32" cy="36" r="2.6" fill={ACCENT} opacity=".55" />
      <path d="M58 36h16" stroke={ACCENT} strokeWidth="2.4" strokeLinecap="round" />
    </>
  );
}

function Switch() {
  return (
    <>
      <rect x="8" y="22" width="80" height="30" rx="4" fill={FILL} stroke="currentColor" strokeWidth="2" />
      <path d="M8 32h80" stroke="currentColor" strokeWidth="1.2" opacity=".45" />
      {[0, 1, 2, 3, 4, 5, 6, 7].map((index) => (
        <rect
          key={index}
          x={16 + index * 8.6}
          y={38}
          width="6"
          height="7"
          rx="1"
          fill={index < 3 ? ACCENT : MUTED}
          opacity={index < 3 ? 1 : 0.45}
        />
      ))}
      <circle cx="80" cy="27" r="2" fill={ACCENT} />
    </>
  );
}

function AccessPoint() {
  return (
    <>
      <path
        d="M33 24a21 21 0 0 1 30 0M26 17a31 31 0 0 1 44 0"
        fill="none"
        stroke={ACCENT}
        strokeWidth="2.4"
        strokeLinecap="round"
      />
      <rect x="26" y="30" width="44" height="22" rx="11" fill={FILL} stroke="currentColor" strokeWidth="2" />
      <circle cx="48" cy="41" r="3.4" fill={ACCENT} />
      <path d="M36 41h4M56 41h4" stroke={MUTED} strokeWidth="2" strokeLinecap="round" />
    </>
  );
}

function Desktop() {
  return (
    <>
      <rect x="14" y="12" width="52" height="34" rx="3" fill={FILL} stroke="currentColor" strokeWidth="2" />
      <path d="M14 39h52" stroke="currentColor" strokeWidth="1.2" opacity=".4" />
      <path d="M22 20h22M22 27h14" stroke={ACCENT} strokeWidth="2" strokeLinecap="round" opacity=".8" />
      <path d="M34 46v6h12v-6" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M26 53h28" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
      <rect x="72" y="18" width="14" height="34" rx="2.5" fill={FILL} stroke="currentColor" strokeWidth="2" />
      <circle cx="79" cy="24" r="1.8" fill={ACCENT} />
    </>
  );
}

function Laptop() {
  return (
    <>
      <rect x="22" y="14" width="52" height="32" rx="3" fill={FILL} stroke="currentColor" strokeWidth="2" />
      <path d="M30 22h20M30 30h30" stroke={ACCENT} strokeWidth="2" strokeLinecap="round" opacity=".75" />
      <path d="M12 52h72l-6-6H18z" fill={FILL} stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
    </>
  );
}

function Server() {
  return (
    <>
      <rect x="20" y="10" width="56" height="44" rx="4" fill={FILL} stroke="currentColor" strokeWidth="2" />
      {[0, 1, 2].map((index) => (
        <g key={index}>
          <path d={`M20 ${24 + index * 14}h56`} stroke="currentColor" strokeWidth="1.2" opacity=".4" />
          <circle cx="29" cy={17 + index * 14} r="2.2" fill={index === 0 ? ACCENT : MUTED} />
          <path d={`M38 ${17 + index * 14}h26`} stroke={MUTED} strokeWidth="2" strokeLinecap="round" opacity=".55" />
        </g>
      ))}
    </>
  );
}

function Phone() {
  return (
    <>
      <rect x="33" y="8" width="30" height="48" rx="5" fill={FILL} stroke="currentColor" strokeWidth="2" />
      <path d="M33 17h30M33 47h30" stroke="currentColor" strokeWidth="1.2" opacity=".45" />
      <path d="M41 27h14M41 34h9" stroke={ACCENT} strokeWidth="2" strokeLinecap="round" opacity=".8" />
      <circle cx="48" cy="51.5" r="2" fill={MUTED} />
    </>
  );
}

function TvMedia() {
  return (
    <>
      <rect x="9" y="11" width="78" height="38" rx="3.5" fill={FILL} stroke="currentColor" strokeWidth="2" />
      <path d="M40 24l16 6-16 6z" fill={ACCENT} />
      <path d="M42 56h12" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
      <path d="M48 49v7" stroke="currentColor" strokeWidth="2" />
    </>
  );
}

function Printer() {
  return (
    <>
      <path d="M30 10h36v14H30z" fill={FILL} stroke="currentColor" strokeWidth="2" />
      <rect x="16" y="24" width="64" height="20" rx="3" fill={FILL} stroke="currentColor" strokeWidth="2" />
      <circle cx="70" cy="31" r="2.4" fill={ACCENT} />
      <path d="M30 40h36v14H30z" fill={FILL} stroke="currentColor" strokeWidth="2" />
      <path d="M37 47h22" stroke={MUTED} strokeWidth="2" strokeLinecap="round" opacity=".6" />
    </>
  );
}

function Camera() {
  return (
    <>
      <path d="M18 22h34a10 10 0 0 1 10 10v10a4 4 0 0 1-4 4H18z" fill={FILL} stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
      <path d="M62 30l16-7v22l-16-7z" fill={FILL} stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
      <circle cx="30" cy="34" r="4.5" fill="none" stroke={ACCENT} strokeWidth="2.2" />
      <path d="M24 46v8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </>
  );
}

function Unknown() {
  return (
    <>
      <path
        d="M48 7 77 22v20L48 57 19 42V22z"
        fill={FILL}
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
        strokeDasharray="5 4"
      />
      <path
        d="M41 27c1-6.5 13-7.5 15-1 2 7-8 8.5-8 14"
        fill="none"
        stroke={ACCENT}
        strokeWidth="3"
        strokeLinecap="round"
      />
      <circle cx="48" cy="47" r="2.2" fill={ACCENT} />
    </>
  );
}

const ART: Record<DeviceType, () => JSX.Element> = {
  router: Router,
  switch: Switch,
  "access-point": AccessPoint,
  desktop: Desktop,
  laptop: Laptop,
  server: Server,
  phone: Phone,
  "tv-media": TvMedia,
  printer: Printer,
  camera: Camera,
  unknown: Unknown,
};

export function deviceArtFor(type: DeviceType): () => JSX.Element {
  return ART[type] || ART.unknown;
}

export default function DeviceArt({
  type,
  label,
  width = 84,
  className = "",
}: {
  type: DeviceType;
  label: string;
  width?: number;
  className?: string;
}) {
  const Art = deviceArtFor(type);
  return (
    <svg
      className={`device-art ${className}`.trim()}
      viewBox="0 0 96 64"
      width={width}
      height={Math.round((width * 64) / 96)}
      role="img"
      aria-label={`${label} (${DEVICE_TYPE_LABELS[type] || "device"})`}
      focusable="false"
    >
      <Art />
    </svg>
  );
}
