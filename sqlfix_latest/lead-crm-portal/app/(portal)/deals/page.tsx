"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  crmEntities,
  DEAL_STATUSES,
  errMessage,
  formatDate,
  formatMoney,
  type Company,
  type Deal,
  type DealStage,
} from "@/lib/crm";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { useChangedIds } from "@/lib/live";
import { deleteView, listViews, saveView, type LocalView } from "@/lib/savedViews";
import { EmptyState, ErrorState } from "@/components/ui/States";
import { EvidenceNote, FreshnessNote } from "@/components/ui/SurfaceMeta";
import { FilterBar, PageHeader, StatusBadge } from "@/components/ui/Primitives";
import { Icon } from "@/components/ui/Icon";

/** Deals with no stage_id (or a stage the tenant deleted) land in this column. */
const UNSTAGED = "__unstaged__";

const VIEW_KEY = "deals";
type DealsView = { q: string; status: string };

/** 2px top rule per column: won = emerald, lost = rust, unstaged = neutral, open stages = navy. */
function columnToneClass(col: { id: string; is_won?: boolean | null; is_lost?: boolean | null }): string {
  if (col.id === UNSTAGED) return "border-t-line-strong";
  if (col.is_won) return "border-t-emerald-600 dark:border-t-emerald-400";
  if (col.is_lost) return "border-t-rust-500 dark:border-t-rust-400";
  return "border-t-brand-600 dark:border-t-brand-300";
}

function DealCard({
  deal,
  stages,
  companyName,
  busy,
  onDragStart,
  onMove,
  flash = false,
}: {
  deal: Deal;
  stages: DealStage[];
  companyName: string;
  busy: boolean;
  onDragStart: (e: React.DragEvent, deal: Deal) => void;
  onMove: (deal: Deal, stageId: string) => void;
  flash?: boolean;
}) {
  return (
    <article
      draggable={!busy}
      onDragStart={(e) => onDragStart(e, deal)}
      className={`cursor-grab rounded-lg border border-line bg-surface-2 p-3 shadow-card transition-colors hover:border-line-strong active:cursor-grabbing ${busy ? "opacity-70" : ""} ${flash ? "row-flash" : ""}`}
    >
      <div className="flex items-start justify-between gap-2">
        <h3 className="line-clamp-2 text-[0.8125rem] font-semibold leading-snug text-ink">{deal.title}</h3>
        <span className="shrink-0 text-[0.8125rem] font-semibold tabular-nums text-ink">
          {formatMoney(deal.value, deal.currency)}
        </span>
      </div>

      <div className="mt-0.5 truncate text-xs text-ink-muted">
        {companyName || <span className="text-ink-faint">Account unavailable</span>}
      </div>

      <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2">
        <StatusBadge status={deal.status || "open"} />
        <span className="text-[11px] tabular-nums text-ink-faint">
          {deal.expected_close_date ? `Close ${formatDate(deal.expected_close_date)}` : "Close date unavailable"}
        </span>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-faint">
        <span className="inline-flex min-w-0 items-center gap-1">
          <Icon name="team" size={12} />
          <span className="truncate">{deal.owner || "Unassigned"}</span>
        </span>
        {typeof deal.probability === "number" ? <span className="tabular-nums">{deal.probability}% probability</span> : null}
        <EvidenceNote count={typeof deal.probability === "number" ? 1 : undefined} label="forecast inputs" />
      </div>

      {/* Keyboard-accessible equivalent of dragging the card. */}
      <div className="mt-2.5 border-t border-line pt-2.5">
        <label className="sr-only" htmlFor={`stage-${deal.id}`}>
          Move {deal.title} to stage
        </label>
        <select
          id={`stage-${deal.id}`}
          className="select h-8 min-h-0 px-2 py-1 text-xs"
          disabled={busy}
          value={deal.stage_id || ""}
          onChange={(e) => {
            if (e.target.value) onMove(deal, e.target.value);
          }}
        >
          <option value="">Unstaged</option>
          {stages.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </div>
    </article>
  );
}

function BoardSkeleton() {
  return (
    <div className="flex gap-3 overflow-hidden" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading deals…</span>
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="w-72 shrink-0 space-y-2 rounded-xl border border-line border-t-2 border-t-line-strong bg-surface-3/40 p-2">
          <div className="skeleton m-1 h-4 w-28" />
          {Array.from({ length: 3 }).map((__, j) => <div key={j} className="skeleton h-24 w-full" />)}
        </div>
      ))}
    </div>
  );
}

export default function DealsPage() {
  const tenantId = getTenantId();
  const [stages, setStages] = useState<DealStage[]>([]);
  const [deals, setDeals] = useState<Deal[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [loading, setLoading] = useState(true);
  const changedDealIds = useChangedIds(deals, (r: any) => String(r.id));
  const loadedOnceRef = useRef(false);
  const [error, setError] = useState<unknown>(null);
  const [mutationError, setMutationError] = useState<unknown>(null);
  const [msg, setMsg] = useState("");
  const [movingId, setMovingId] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState<string | null>(null);

  const [formOpen, setFormOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const emptyForm = {
    title: "",
    value: "",
    currency: "USD",
    company_id: "",
    stage_id: "",
    status: "open",
    probability: "",
    expected_close_date: "",
  };
  const [form, setForm] = useState(emptyForm);

  // Board filters. A 300-deal board is unreadable without them, and the saved
  // views make a filter combination reusable instead of re-typed every visit.
  const [q, setQ] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [views, setViews] = useState<LocalView<DealsView>[]>([]);

  useEffect(() => {
    setViews(listViews<DealsView>(VIEW_KEY));
  }, []);

  const load = useCallback(() => {
    if (!tenantId) {
      setLoading(false);
      setError("No workspace selected.");
      return;
    }
    if (!loadedOnceRef.current) setLoading(true);
    Promise.all([
      crmEntities.listDealStages(tenantId),
      crmEntities.listDeals(tenantId, { limit: 500 }),
      crmEntities.listCompanies(tenantId, { limit: 200 }).catch(() => ({ total: 0, rows: [] })),
    ])
      .then(([stageRows, dealPage, companyPage]) => {
        setStages(stageRows);
        setDeals(dealPage.rows);
        setCompanies(companyPage.rows);
        setError("");
      })
      .catch((e: unknown) => setError(e))
      .finally(() => { loadedOnceRef.current = true; setLoading(false); });
  }, [tenantId]);

  useEffect(() => {
    load();
  }, [load]);

  useCrmChange(load, { tables: ["deals", "deal_stages", "companies"] });

  const companyNameById = useMemo(() => {
    const m = new Map<string, string>();
    companies.forEach((c) => m.set(c.id, c.name));
    return m;
  }, [companies]);

  function companyLabel(d: Deal): string {
    return d.company_name || (d.company_id ? companyNameById.get(d.company_id) || "" : "");
  }

  /** Client-side filter — the board already loads its full page of deals, so
   *  filtering here avoids a refetch per keystroke. */
  const visibleDeals = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return deals.filter((d) => {
      if (statusFilter && (d.status || "open") !== statusFilter) return false;
      if (!needle) return true;
      return (
        d.title.toLowerCase().includes(needle) ||
        companyLabel(d).toLowerCase().includes(needle)
      );
    });
    // companyLabel depends on companyNameById, which is memoised on `companies`.
  }, [deals, q, statusFilter, companyNameById]);

  /** Group deals by stage; unknown/absent stage_id falls into the UNSTAGED bucket. */
  const byStage = useMemo(() => {
    const known = new Set(stages.map((s) => s.id));
    const groups = new Map<string, Deal[]>();
    stages.forEach((s) => groups.set(s.id, []));
    groups.set(UNSTAGED, []);
    visibleDeals.forEach((d) => {
      const key = d.stage_id && known.has(d.stage_id) ? d.stage_id : UNSTAGED;
      groups.get(key)?.push(d);
    });
    return groups;
  }, [visibleDeals, stages]);

  /** Persist first, then replace the card with the API's authoritative row. */
  const moveDeal = useCallback(
    async (deal: Deal, stageId: string) => {
      if (!tenantId || stageId === UNSTAGED || stageId === deal.stage_id) return;
      const stageName = stages.find((s) => s.id === stageId)?.name || "stage";
      setMovingId(deal.id);
      setMutationError(null);
      setMsg("");

      try {
        const authoritative = await crmEntities.changeDealStage(tenantId, deal.id, stageId);
        setDeals((prev) => prev.map((d) => (d.id === deal.id ? authoritative : d)));
        setMsg(`Moved "${deal.title}" → ${stageName}`);
      } catch (e: unknown) {
        setMutationError(e);
        setMsg("");
      } finally {
        setMovingId(null);
      }
    },
    [tenantId, stages]
  );

  function onDragStart(e: React.DragEvent, deal: Deal) {
    e.dataTransfer.setData("text/deal_id", deal.id);
    e.dataTransfer.effectAllowed = "move";
  }

  function onDragOver(e: React.DragEvent, stageId: string) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDragOver(stageId);
  }

  function onDrop(e: React.DragEvent, stageId: string) {
    e.preventDefault();
    setDragOver(null);
    const dealId = e.dataTransfer.getData("text/deal_id");
    const deal = deals.find((d) => d.id === dealId);
    if (deal) void moveDeal(deal, stageId);
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!form.title.trim()) {
      setMsg("Deal title is required");
      return;
    }
    setSaving(true);
    setMsg("");
    try {
      await crmEntities.createDeal(tenantId, {
        title: form.title.trim(),
        value: form.value ? Number(form.value) : null,
        currency: form.currency || "USD",
        company_id: form.company_id || null,
        stage_id: form.stage_id || null,
        status: form.status,
        probability: form.probability ? Number(form.probability) : null,
        expected_close_date: form.expected_close_date || null,
      });
      setMsg(`Created ${form.title.trim()}`);
      setFormOpen(false);
      setForm(emptyForm);
      load();
    } catch (err: unknown) {
      setMutationError(err);
      setMsg("");
    } finally {
      setSaving(false);
    }
  }

  const columns: Array<{ id: string; name: string; color?: string | null; is_won?: boolean | null; is_lost?: boolean | null }> = [
    ...stages.map((s) => ({ id: s.id, name: s.name, color: s.color, is_won: s.is_won, is_lost: s.is_lost })),
    ...((byStage.get(UNSTAGED)?.length ?? 0) > 0
      ? [{ id: UNSTAGED, name: "Unstaged", color: null }]
      : []),
  ];

  if (loading) {
    return (
      <div className="page">
        <PageHeader eyebrow="Revenue" title="Deals" description="Move opportunities through the deal pipeline." />
        <BoardSkeleton />
      </div>
    );
  }

  if (error) {
    return (
      <div className="page">
        <PageHeader eyebrow="Revenue" title="Deals" description="Move opportunities through the deal pipeline." />
        <ErrorState error={error} onRetry={load} />
      </div>
    );
  }

  const openPipelineValue = visibleDeals.reduce((acc, d) => acc + ((d.status || "open") === "open" ? d.value || 0 : 0), 0);

  return (
    <div className="page">
      <PageHeader
        eyebrow="Revenue"
        title="Deals"
        description={
          <>
            <span className="tabular-nums">
              {visibleDeals.length === deals.length
                ? `${deals.length} opportunities`
                : `${visibleDeals.length} of ${deals.length} shown`}
            </span>{" "}
            · move work through the deal pipeline; lead lifecycle remains separate.
          </>
        }
        meta={
          <>
            <FreshnessNote label="Live CRM board" detail={`${deals.length} loaded`} />
            <span className="tabular-nums">Open value {formatMoney(openPipelineValue, visibleDeals[0]?.currency || "USD")}</span>
            <span>Forecast totals use only deal values returned by the API.</span>
            <span aria-live="polite" className="text-ink-muted">
              {movingId ? "Saving…" : msg}
            </span>
          </>
        }
        actions={
          <>
            <button type="button" className="btn-secondary" onClick={load}>
              <Icon name="refresh" size={14} />
              Refresh
            </button>
            <button type="button" className="btn-primary" onClick={() => setFormOpen(true)}>
              <Icon name="plus" size={14} />
              New deal
            </button>
          </>
        }
      />

      {mutationError ? <ErrorState error={mutationError} onRetry={() => { setMutationError(null); void load(); }} /> : null}

      <FilterBar>
        <label className="sr-only" htmlFor="deal-search">
          Search deals
        </label>
        <div className="relative w-full sm:w-64">
          <Icon name="search" size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-faint" />
          <input
            id="deal-search"
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search deal or company…"
            className="input h-9 pl-8"
          />
        </div>
        <label className="sr-only" htmlFor="deal-status-filter">
          Filter by status
        </label>
        <select
          id="deal-status-filter"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="select h-9 w-full capitalize sm:w-40"
        >
          <option value="">All statuses</option>
          {DEAL_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>

        {(q || statusFilter) && (
          <button
            type="button"
            className="btn-ghost btn-sm"
            onClick={() => {
              setQ("");
              setStatusFilter("");
            }}
          >
            Clear
          </button>
        )}

        <span className="mx-1 hidden h-5 w-px bg-line sm:block" aria-hidden="true" />

        {views.map((v) => (
          <span key={v.name} className="inline-flex items-center rounded-md border border-line bg-surface-3">
            <button
              type="button"
              onClick={() => {
                setQ(v.state.q);
                setStatusFilter(v.state.status);
              }}
              className="px-2 py-1 text-xs font-medium text-ink-muted hover:text-ink"
            >
              {v.name}
            </button>
            <button
              type="button"
              aria-label={`Delete view ${v.name}`}
              onClick={() => setViews(deleteView<DealsView>(VIEW_KEY, v.name))}
              className="border-l border-line px-1.5 py-1 text-ink-faint hover:text-rust-600 dark:hover:text-rust-300"
            >
              <Icon name="close" size={12} />
            </button>
          </span>
        ))}

        <button
          type="button"
          className="btn-ghost btn-sm"
          disabled={!q && !statusFilter}
          onClick={() => {
            const name = window.prompt("Name this view");
            if (name) setViews(saveView<DealsView>(VIEW_KEY, name, { q, status: statusFilter }));
          }}
        >
          Save view
        </button>
      </FilterBar>

      {stages.length === 0 ? (
        <div className="card"><EmptyState title="Deal stages are unavailable" hint="No deal-stage rows were returned for this workspace, so LeadX cannot draw the board yet. Ask an operator to seed the deal pipeline." /></div>
      ) : visibleDeals.length === 0 ? (
        <div className="card"><EmptyState title="No deals match this view" hint="Clear the search or status filter. A missing value or account is shown as unavailable rather than inferred." /></div>
      ) : (
        <div className="min-w-0 overflow-x-auto pb-2" role="region" aria-label="Deal board">
          <div className="flex w-max gap-3">
            {columns.map((col) => {
              const items = byStage.get(col.id) || [];
              const sum = items.reduce((acc, d) => acc + (d.value || 0), 0);
              const isOver = dragOver === col.id;
              return (
                <section
                  key={col.id}
                  onDragOver={(e) => onDragOver(e, col.id)}
                  onDragLeave={() => setDragOver(null)}
                  onDrop={(e) => onDrop(e, col.id)}
                  aria-label={`${col.name} — ${items.length} deals`}
                  className={`flex w-[17.5rem] shrink-0 flex-col rounded-xl border border-t-2 bg-surface-3/50 transition-colors sm:w-72 ${columnToneClass(col)} ${
                    isOver ? "border-x-brand-400 border-b-brand-400 bg-brand-50/60 dark:border-x-brand-500/60 dark:border-b-brand-500/60 dark:bg-brand-500/10" : "border-x-line border-b-line"
                  }`}
                >
                  <header className="flex items-start justify-between gap-2 border-b border-line px-3 py-2.5">
                    <div className="min-w-0">
                      <h2 className="flex items-center gap-1.5 truncate text-[0.8125rem] font-semibold text-ink">
                        {col.color ? <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: col.color }} aria-hidden="true" /> : null}
                        <span className="truncate">{col.name}</span>
                      </h2>
                      <div className="mt-0.5 text-xs font-medium tabular-nums text-ink-muted">
                        {formatMoney(sum, items[0]?.currency || "USD")}
                      </div>
                    </div>
                    <span className="badge-neutral tabular-nums">{items.length}</span>
                  </header>

                  <div className="max-h-[calc(100vh-280px)] min-h-[160px] flex-1 space-y-2 overflow-y-auto p-2">
                    {items.map((d) => (
                      <DealCard
                        key={d.id}
                        deal={d}
                        stages={stages}
                        companyName={companyLabel(d)}
                        busy={movingId === d.id}
                        onDragStart={onDragStart}
                        onMove={moveDeal}
                        flash={changedDealIds.has(String(d.id))}
                      />
                    ))}
                    {items.length === 0 && (
                      <p className={`rounded-lg border border-dashed py-8 text-center text-xs ${isOver ? "border-brand-400 text-brand-700 dark:text-brand-300" : "border-line-strong text-ink-faint"}`}>
                        Drop deals here
                      </p>
                    )}
                  </div>
                </section>
              );
            })}
          </div>
        </div>
      )}

      {formOpen && (
        <div
          className="dialog-layer"
          role="dialog"
          aria-modal="true"
          aria-labelledby="new-deal-title"
        >
          <form
            onSubmit={handleCreate}
            className="flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-xl border border-line bg-surface-2 shadow-lift"
          >
            <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-4">
              <div>
                <div className="eyebrow">Revenue</div>
                <h2 id="new-deal-title" className="mt-1 text-base font-semibold text-ink">
                  New deal
                </h2>
              </div>
              <button type="button" className="btn-ghost btn-sm" onClick={() => setFormOpen(false)}>
                Close
              </button>
            </div>

            <div className="grid gap-4 overflow-y-auto px-5 py-5 sm:grid-cols-2">
              <div className="sm:col-span-2">
                <label className="field-label" htmlFor="df-title">
                  Title *
                </label>
                <input
                  id="df-title"
                  className="input"
                  required
                  value={form.title}
                  onChange={(e) => setForm({ ...form, title: e.target.value })}
                />
              </div>
              <div>
                <label className="field-label" htmlFor="df-value">
                  Value
                </label>
                <input
                  id="df-value"
                  type="number"
                  min="0"
                  step="0.01"
                  className="input tabular-nums"
                  value={form.value}
                  onChange={(e) => setForm({ ...form, value: e.target.value })}
                />
              </div>
              <div>
                <label className="field-label" htmlFor="df-currency">
                  Currency
                </label>
                <input
                  id="df-currency"
                  className="input uppercase"
                  maxLength={3}
                  value={form.currency}
                  onChange={(e) => setForm({ ...form, currency: e.target.value.toUpperCase() })}
                />
                <p className="field-hint">Three-letter ISO code.</p>
              </div>
              <div className="sm:col-span-2">
                <label className="field-label" htmlFor="df-company">
                  Company
                </label>
                <select
                  id="df-company"
                  className="select"
                  value={form.company_id}
                  onChange={(e) => setForm({ ...form, company_id: e.target.value })}
                >
                  <option value="">No company</option>
                  {companies.map((co) => (
                    <option key={co.id} value={co.id}>
                      {co.name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="field-label" htmlFor="df-stage">
                  Stage
                </label>
                <select
                  id="df-stage"
                  className="select"
                  value={form.stage_id}
                  onChange={(e) => setForm({ ...form, stage_id: e.target.value })}
                >
                  <option value="">Unstaged</option>
                  {stages.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="field-label" htmlFor="df-status">
                  Status
                </label>
                <select
                  id="df-status"
                  className="select capitalize"
                  value={form.status}
                  onChange={(e) => setForm({ ...form, status: e.target.value })}
                >
                  {DEAL_STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="field-label" htmlFor="df-prob">
                  Probability (%)
                </label>
                <input
                  id="df-prob"
                  type="number"
                  min="0"
                  max="100"
                  className="input tabular-nums"
                  value={form.probability}
                  onChange={(e) => setForm({ ...form, probability: e.target.value })}
                />
              </div>
              <div>
                <label className="field-label" htmlFor="df-close">
                  Expected close
                </label>
                <input
                  id="df-close"
                  type="date"
                  className="input tabular-nums"
                  value={form.expected_close_date}
                  onChange={(e) => setForm({ ...form, expected_close_date: e.target.value })}
                />
              </div>
            </div>

            <div className="flex justify-end gap-2 border-t border-line bg-surface-3/50 px-5 py-4">
              <button type="button" className="btn-secondary" onClick={() => setFormOpen(false)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className="btn-primary">
                {saving ? "Saving…" : "Create deal"}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
