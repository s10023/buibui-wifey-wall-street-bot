---
name: frontend-svelte
description: >
  Svelte 5 + Vite UI workflow for `web/ui/` — dev server, production build,
  page/component layout, store conventions, lightweight-charts integration.
  Pairs with `/frontend-design` (load that first for visual / UX work).
  Invoke when the user says "/frontend-svelte", touches anything under
  `web/ui/`, asks to add a page / component / store, debug Svelte runes, or
  wire a new API endpoint into the UI.
allowed-tools: Bash, Read, Edit
---

# Frontend — Svelte 5 + Vite UI

The web UI lives in `web/ui/`, talks to FastAPI at `web/api/`, and ships as a
production bundle served by the FastAPI backend at `/`.

**Always load `/frontend-design` first** for any visual or UX work — this skill
covers wiring and conventions, not aesthetics.

## Stack

| Piece | Version / role |
| ------- | ---------------- |
| Svelte | 5 (uses runes — `$state`, `$derived`, `$effect`) |
| Vite | 8 |
| TypeScript | 5 |
| `lightweight-charts` | 4.2 — only in `CandleChart.svelte` and `Chart.svelte` overlays |
| `svelte-check` | type checker (`make web-check`) |

No CSS framework — plain CSS in `app.css` + per-component `<style>` blocks.

## Directory layout

```text
web/ui/src/
├── api.ts              fetch helpers; one function per endpoint
├── app.css             global styles, CSS variables, theme tokens
├── App.svelte          shell + router (page switch via active-tab store)
├── main.ts             Svelte mount point
├── components/         reusable UI pieces (no page-level state)
│   ├── AnalysisCard.svelte
│   ├── BacktestResult.svelte
│   ├── CandleChart.svelte    ← lightweight-charts wrapper
│   ├── ErrorBanner.svelte
│   ├── LoadingSpinner.svelte
│   ├── Nav.svelte
│   ├── PositionRow.svelte
│   └── PriceRow.svelte
├── pages/              one file per top-level tab
│   ├── Backtest.svelte
│   ├── Chart.svelte
│   ├── Positions.svelte
│   ├── Prices.svelte
│   ├── SignalFeed.svelte
│   └── Stats.svelte
└── stores/             writable / derived stores; one concern per file
    ├── activeConfig.ts
    ├── config.ts
    ├── positions.ts
    ├── prices.ts
    ├── signals.ts
    ├── strategies.ts
    └── watchlist.ts
```

## Common commands

```bash
# Install / reinstall deps after a package.json change
make web-install            # cd web/ui && npm install

# Dev server (HMR, default port 5173 — set DEV_PORT to override)
make web-dev                # cd web/ui && vite

# Production build (outputs to web/ui/dist/, served by FastAPI)
make web-build              # cd web/ui && vite build

# Type check Svelte + TS
make web-check              # cd web/ui && svelte-check

# Build + start backend serving the bundle
make web-full               # web-build + wifey-web

# Backend only (use with web-dev for split-port development)
make wifey-web PORT=8000
```

For CI / commit, the relevant gate is `make web-build` — it must succeed before
merging any UI change. Keep `make web-check` clean too (no untyped props, no
missing event types).

## Adding a new page (tab)

1. Create `web/ui/src/pages/Foo.svelte`.
2. Register the tab in `App.svelte` (or wherever the tab list lives) and wire
   it into the page switch.
3. Add any new fetch in `api.ts` — one function per endpoint, returning a
   typed promise.
4. If the page has shared state, add a store under `stores/foo.ts`. Use
   `writable` for mutable, `derived` for computed. Stores load once at app
   start and update on user actions or polling.
5. Use existing components (`LoadingSpinner`, `ErrorBanner`) for loading and
   error states — don't reinvent them.

## Adding a new component

- One concern per component. If it's >200 lines, split.
- Props use TypeScript runes: `let { foo, bar }: Props = $props();` with a
  `type Props = { ... }` above.
- Local state uses `$state(...)`. Derived values use `$derived(...)`. Side
  effects use `$effect(() => { ... })`.
- Emit events via callback props (Svelte 5 idiom): `let { onclick } = $props();`
  rather than `createEventDispatcher`.

## Wiring a new API endpoint

1. Add the route in `web/api/routers/<router>.py` and register it in
   `web/api/main.py`.
2. Add a Pydantic model under `web/api/models/` (or inline if trivial).
3. Add the typed fetcher in `web/ui/src/api.ts`:

   ```ts
   export type FooResponse = { ... };
   export async function fetchFoo(symbol: string): Promise<FooResponse> {
     const r = await fetch(`/api/foo?symbol=${symbol}`);
     if (!r.ok) throw new Error(`fetchFoo: ${r.status}`);
     return r.json();
   }
   ```

4. Call from page/component `onMount` or via a store.

## Charts (lightweight-charts)

- All chart code lives in `components/CandleChart.svelte` and chart-specific
  overlay logic in `pages/Chart.svelte`.
- Series types in use: candlestick, line (EMA, BOS, EQH/EQL), markers (signal
  arrows). HTML divs are layered for FVG / OB / Fib / OTE rectangles.
- Every overlay zone carries a `close_ms` so it can be filtered when the
  user scrubs time.
- See `/stats-dashboard` for the related Stats panel that complements the
  Chart tab.

## Styling

- Use CSS custom properties from `app.css` (theme tokens) — don't hard-code
  colors or spacing.
- Component-scoped styles via `<style>` block. Avoid global selectors.
- For new visual designs, **always load `/frontend-design`** before writing
  CSS — it covers spacing, contrast, motion, and the project's aesthetic
  direction.

## Pre-commit checklist (UI changes)

- `make web-check` — clean
- `make web-build` — succeeds (this is what CI runs)
- Manually click through the changed page in `make web-dev` — golden path +
  one error case
- If a new endpoint was added: confirm it appears in the FastAPI auto-docs
  (`/docs`)

## Implementation files

| File | Role |
| ------ | ------ |
| `web/ui/package.json` | deps, scripts |
| `web/ui/vite.config.ts` | dev server proxy to FastAPI; build output dir |
| `web/ui/src/api.ts` | typed fetch helpers — single source of truth for endpoints |
| `web/ui/src/App.svelte` | shell, router, tab switch |
| `web/api/main.py` | FastAPI app; serves `web/ui/dist/` at `/` |
| `web/api/routers/` | one router per resource group |
| `web/api/models/` | Pydantic response models |
| `Makefile` | `web-install`, `web-dev`, `web-build`, `web-check`, `web-preview`, `web-full`, `wifey-web` |

## Related

- `/frontend-design` — visual / UX direction. Load before any styling work.
- `/stats-dashboard` — Stats page architecture and `stats_lib.py` contract.
