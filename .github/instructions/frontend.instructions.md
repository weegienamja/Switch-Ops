---
applyTo: "frontend/**/*.{ts,tsx,css}"
---

# Frontend instructions

- Next.js App Router + TypeScript (strict).
- Motion for React for animations. Staggered card entrance, animated topology, health pulse only when healthy.
- Respect `prefers-reduced-motion`.
- Dark navy/charcoal palette. Glassy cards, thin borders, subtle radial glow background, network grid texture.
- Status colours: green healthy, amber warning, red critical.
- Monospace blocks (`JetBrains Mono` / `ui-monospace`) for raw command output.
- **No emoji. No generic SaaS purple gradient. No toy styling.**
- Components handle `loading`, `error`, and `empty` states explicitly. Reuse `LoadingState` and `ErrorState`.
- All API calls go through `lib/api.ts`. Types live in `lib/types.ts`.
- Setup wizard is shown when `/api/setup/status` reports no credentials.
- Safe Control Panel is read-only unless backend reports `enableWriteActions: true`. Always show a confirmation modal for write actions and display before/after state.
- Display the "Local lab dashboard. Do not expose publicly." warning persistently.
