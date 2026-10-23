# LeadX Frontend Revamp and Realtime Audit

Date: 2026-10-23

## Design system

The portal now uses the existing warm-neutral/deep-teal/tangerine identity with consolidated shared primitives rather than a new visual framework. Shared layers include:

- Editorial page headers and quiet breadcrumbs.
- Dense `DataTable`, filter bars, pagination, notices, drawers, confirmation dialogs, and operation state cards.
- Consistent semantic status colors: green success, amber pending/attention, red failure/blocker, gray unavailable.
- Monospaced IDs/timing/provider metadata and restrained display typography.
- Explicit focus states, reduced-motion handling, mobile navigation, and `aria-current` navigation state.
- Separate client workspace and operator control-plane visual hierarchy.

## Realtime contract

The browser keeps one private tenant Broadcast channel and treats events as invalidation hints. Pages refetch authoritative tenant-scoped API projections; no realtime payload is treated as a database row. Reconnect and visibility restoration trigger reconciliation. Visible polling is bounded by the configured deadline and pauses while the tab is hidden. Refreshes never start discovery, enrichment, email, Stripe, or provider operations.

The frontend preserves the current `useCrmChange` filters, `useLiveLoader` deadlines, request IDs, CSRF/idempotency headers, session gates, and tenant authorization boundaries. Direct database-trigger Broadcast still requires the ordered Supabase realtime migration to be applied remotely.

## Surface coverage

### Client workspace

- Dashboard: concise overview, real CRM KPIs, quota, funnel, quality, tasks, activity, and live invalidation.
- Growth: mission/request workspace, quota, discovery status, full operation funnel, learning recommendations, quality, delivery, pipeline, replies, and deals.
- Search/Discovery: approval-gated request flow and truthful planner/run status.
- Intelligence: provider configured-vs-executable truth, free/open-vs-paid lane catalog, planner-only preview, and no fake persisted results.
- Leads: dense table, filters, selection/bulk actions, evidence/freshness/status, realtime refresh.
- Companies/Contacts: CRM entity tables, tenant-scoped filters, pagination, forms, realtime refresh.
- Deals/Pipeline: existing board/CRM operations retained with shared visual primitives.
- Activities/Today/Reports/Deal Reports: actionable operational layouts, real API metrics, bounded refresh/deadline states.
- Sequences/Replies/Automations/Agents: operational status, runs, retries/errors, approval, and live refresh states.
- Approvals/Import/Logs: explicit success/error/empty states, dense data surfaces, and tenant-scoped operations.
- Settings/Email/Integrations/Team/Billing: truthful configuration, connection, usage, and unavailable-state presentation; no invented invoice/payment capabilities.

### Admin control plane

Admin overview, health, jobs, runs, pipeline, tenants, usage, security/audit, notifications, search, support, keys, suppression, territory, scale, replies, intelligence, automation, autonomous growth, prospecting, and tenant detail surfaces now share operator headers, status semantics, dense controls, bounded polling/deadline handling, and existing admin authentication.

## Validation

- `npm run typecheck`: passed.
- `npm run build`: passed; 69 static pages generated.
- Focused portal contracts: 8 passed; 10 authenticated/runtime cases skipped because no configured staging URL/storage state was available.
- No provider, email, Stripe, or Supabase write was performed by frontend refresh/realtime behavior.

## Honest limitations

- Live authenticated browser validation requires `LEADX_E2E_URL` and `LEADX_E2E_STORAGE_STATE`.
- Direct database-trigger realtime requires applying `supabase/migrations/20261020001600_leadx_21_realtime_capture.sql` to the existing Supabase project.
- Admin platform-wide changes intentionally use bounded authenticated polling rather than an unsafe tenant-wide Broadcast channel.
