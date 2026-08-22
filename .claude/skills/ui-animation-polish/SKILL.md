---
name: ui-animation-polish
description: Visual language and animation rules for the SwitchOps dashboard.
---

# UI / animation polish skill

## Palette

- Background base: `#0a0f1a` (deep navy) with a radial glow centred top, colour `rgba(34, 197, 94, 0.08)` fading to transparent.
- Surface: `rgba(15, 23, 36, 0.7)` glass with a 1px border `rgba(148, 163, 184, 0.12)` and `backdrop-filter: blur(14px)`.
- Text primary: `#e2e8f0`; secondary: `#94a3b8`; muted: `#64748b`.
- Status: green `#22c55e`, amber `#f59e0b`, red `#ef4444`, accent cyan `#22d3ee`.

## Typography

- Sans: `Inter`, system-ui.
- Mono: `JetBrains Mono`, `ui-monospace`.

## Animation rules

- Cards enter staggered (`delay: index * 0.05`, `duration: 0.35`, `ease: "easeOut"`).
- Health pulse animates only when the switch is healthy. Stop animating on amber/red.
- Connected ports glow slowly (3 s loop). Shutdown ports are 40% opacity, no animation.
- Always check `prefers-reduced-motion` and disable animation when set.

## Forbidden

- Emoji of any kind.
- Generic SaaS purple-pink gradient.
- Bouncy/playful easings.
- Toy iconography.

## Layout

- 12-column grid on desktop, 4-column on tablet, single column on narrow.
- Persistent header with hostname, model, management IP, IOS version, connection state, mock-mode badge, refresh button.
- Persistent footer banner: "Local lab dashboard. Do not expose publicly."
