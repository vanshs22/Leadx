# LeadX frontend revamp guide ("Ink & Brass")

Portal root: `c:\Users\vansh\Leadx-1\sqlfix_latest\lead-crm-portal`

The foundation is DONE and must not be edited by page agents:
`app/globals.css`, `tailwind.config.js`, `app/layout.tsx`, `app/(portal)/layout.tsx`, `app/(admin)/layout.tsx`,
`components/ui/Primitives.tsx`, `components/ui/States.tsx`, `components/ui/Icon.tsx`, `components/layout/Sidebar.tsx`,
`components/layout/AdminSidebar.tsx`, `components/layout/NotificationBell.tsx`, `components/layout/RealtimeStatus.tsx`,
`components/theme/ThemeProvider.tsx`, everything in `lib/`.
Read `app/globals.css` and `components/ui/Primitives.tsx` first so you know the vocabulary.

## Identity
- Deep navy ink (`brand-*`, primary actions, links, selected state). Brass (`signal-*` / `accent-*`) only for opportunity
  signals: tier A, "why now", upgrade, top-score highlights. Never decorate with brass.
- Status colours: emerald = success, amber = warning/partial, `rust-*` = danger, neutral = cancelled/unknown.
- Warm porcelain neutrals: `bg-surface` (canvas), `bg-surface-2` (cards), `bg-surface-3` (subtle fill), `border-line`,
  `border-line-strong`, `text-ink`, `text-ink-muted`, `text-ink-faint`.
- NO: hardcoded hex/rgb colours in pages, gradients, glows, glassmorphism, emoji as icons, `slate-*`/`gray-*`/`indigo-*`/
  `teal-*`/`cyan-*`/`violet-*`/`purple-*` classes, `rounded-2xl`+ on cards, `shadow-lg/xl`, hover translate/lift on
  data cards, ALL-CAPS mono micro labels with wide tracking. Replace these when you find them.

## Typography (Inter Variable, optical sizing; JetBrains Mono for IDs/code only)
- Page title: `PageHeader` (renders `.page-title`). One h1 per page.
- Section/panel title: `.section-title` or `Panel title=...` (15px/600). Sub copy: `.section-sub`.
- Labels/eyebrows/table heads: `.label` / `.eyebrow` / `.stat-label` (11px, 600, uppercase, .07em). Do not add font-mono.
- Body: 13–14px (`text-[0.8125rem]` / `text-sm`), `text-ink-muted` for secondary.
- Every number, count, currency, %, date column: `tabular-nums`. KPI values: `StatCard` or `.stat-value`.
- IDs, request ids, run ids, emails in code contexts, API keys: `font-mono text-xs`.
- Sentence case for headings/buttons ("Run pipeline", not "RUN PIPELINE" or "Run Pipeline").

## Layout contract (every authenticated page)
```tsx
<div className="page">
  <PageHeader eyebrow="Group" title="Page" description="One sentence of purpose." actions={...} meta={...} />
  <StatGrid cols={4}>{/* StatCard ... optional */}</StatGrid>
  <Panel title="..." description="..." actions={...} bodyClassName="p-0">...table or list...</Panel>
</div>
```
- Spacing rhythm: `page` gives `space-y-6`; inside panels use `gap-3`/`gap-4`; panel body padding `p-5` (or `p-0` for tables).
- Grids: `grid gap-4 lg:grid-cols-3` with main `lg:col-span-2` + side column for detail pages.
- Tables: `DataTable` primitive or `.data-table-wrap > table.data-table`. Numeric columns right-aligned + `tabular-nums`.
  Row actions as `btn-ghost btn-sm` / `btn-secondary btn-sm`. Long tables scroll inside their wrapper, never break page width.
- Status: `StatusBadge status={row.status}` everywhere (consistent colours); other chips: `Badge tone=...`.
- Filters: `FilterBar` with `.input`/`.select` controls (`h-9` sized via classes already). Tabs: `Tabs` primitive.
- Empty/loading/error: `EmptyState`, `LoadingRows`, `ErrorState`, `DataState` from `components/ui/States`.
  Loading must never look like empty; error must never look like empty.
- Buttons: `.btn-primary` (one per view region), `.btn-secondary`, `.btn-ghost`, `.btn-danger`, `.btn-accent` (upgrade/high
  opportunity only), add `btn-sm` in dense rows. Icons from `components/ui/Icon` (`<Icon name=... size={14} />`).
- Forms: `.field-label`, `.input`/`.select`/`.textarea`, `.field-hint`; group in `Panel`; primary submit right-aligned.
- Drawers/dialogs: `Drawer`, `ConfirmDialog`. Notices: `Notice tone=...`.
- Responsive: must work at 375px, 768px, 1280px, 1600px. No horizontal page scroll; wide content scrolls in its container.
- Dark mode: only token classes + `dark:` variants of the brand/signal/emerald/amber/rust scales.

## Hard rules (breaking these fails the release)
1. Presentation only. Do NOT change data fetching, API calls/arguments, `useLiveLoader`/`useCrmChange`/realtime usage,
   state machines, idempotency, polling intervals, routing, auth, or business logic. You may restructure JSX freely.
2. Keep every `aria-*` attribute, `role`, `id`, `data-testid`, `htmlFor`, and button accessible name (move them with the
   element). Never lower the number of `<button>` elements or `aria-` attributes overall; add aria-labels to icon-only
   buttons. The accessibility gate requires >=200 aria attributes and >=200 named buttons repo-wide.
3. Before editing any file, search `sqlfix_latest/tests` and `sqlfix_latest/scripts` for its path/filename and preserve
   every asserted substring exactly (e.g. `useRef` near the top of companies/contacts, `pollError`,
   `Unable to read live run status`, `params.get("run_id")`, `/public/plans`, `useLiveLoader`, `onDeadline`,
   `Load older logs`, `next_before`, `crmFabric`). Keep `"use client"` as the first line where present.
4. No new npm dependencies. No `setInterval` (use existing `lib/live` helpers only if already used). No `window.location.reload`.
5. Only edit files you were assigned. If you need a shared primitive that is missing, implement it locally in your file
   and mention it in your report — do not edit foundation files.
6. Validate with `npx tsc --noEmit` in the portal root (run it; must be clean for your files). Do NOT run `npm run build`
   or `npm install` (other agents are working concurrently). Then run:
   `python -X utf8 -m pytest -q --tb=short tests` from `c:\Users\vansh\Leadx-1\sqlfix_latest` is slow; instead run only the
   tests that reference your files (grep), plus `python scripts/check_frontend_accessibility.py` and
   `python scripts/check_frontend_performance.py`.
7. Report: files changed, what changed visually per page, any test-asserted strings preserved, validation output, and
   anything you could not finish. Be honest; do not claim browser verification you did not perform.
