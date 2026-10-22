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
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/States";
import { EvidenceNote, FreshnessNote, UnavailableNote } from "@/components/ui/SurfaceMeta";

/** Deals with no stage_id (or a stage the tenant deleted) land in this column. */
const UNSTAGED = "__unstaged__";

const VIEW_KEY = "deals";
type DealsView = { q: string; status: string };

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
      className={`cursor-grab rounded-xl border border-line bg-surface-2 p-3 transition hover:shadow-lift active:cursor-grabbing ${flash ? "row-flash" : ""}`}
    >
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-sm font-semibold leading-snug text-ink">{deal.title}</h3>
        <span className="shrink-0 text-sm font-semibold tabular-nums text-ink">
          {formatMoney(deal.value, deal.currency)}
        </span>
      </div>

      <div className="mt-1 truncate text-xs text-ink-muted">
        {companyName || "No company"}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-ink-faint">
        {companyName ? <span>Account linked</span> : <UnavailableNote label="Account" />}
        {deal.expected_close_date ? <span>Close date {formatDate(deal.expected_close_date)}</span> : <span>Close date unavailable</span>}
        <EvidenceNote count={typeof deal.probability === "number" ? 1 : undefined} label="forecast inputs" />
      </div>

      {/* Keyboard-accessible equivalent of dragging the card. */}
      <div className="mt-2">
        <label className="sr-only" htmlFor={`stage-${deal.id}`}>
          Move {deal.title} to stage
        </label>
        <select
          id={`stage-${deal.id}`}
          className="input px-2 py-1 text-xs"
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

export default function DealsPage() {
  const tenantId = getTenantId();
  const [stages, setStages] = useState<DealStage[]>([]);
  const [deals, setDeals] = useState<Deal[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [loading, setLoading] = useState(true);
  const changedDealIds = useChangedIds(deals, (r: any) => String(r.id));
  const loadedOnceRef = useRef(false);
  const [error, setError] = useState("");
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
      .catch((e: unknown) => setError(errMessage(e)))
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

  /**
   * Optimistic move: patch local state first, call the API, roll back the
   * single card to its previous stage if the request fails.
   */
  const moveDeal = useCallback(
    async (deal: Deal, stageId: string) => {
      // UNSTAGED is a synthetic column — there is no stage row to move into.
      if (!tenantId || stageId === UNSTAGED || stageId === deal.stage_id) return;
      const previousStageId = deal.stage_id ?? null;
      const stageName = stages.find((s) => s.id === stageId)?.name || "stage";

      setDeals((prev) =>
        prev.map((d) => (d.id === deal.id ? { ...d, stage_id: stageId, stage_name: stageName } : d))
      );
      setMovingId(deal.id);
      setMsg("");

      try {
        await crmEntities.changeDealStage(tenantId, deal.id, stageId);
        setMsg(`Moved "${deal.title}" → ${stageName}`);
      } catch (e: unknown) {
        setDeals((prev) =>
          prev.map((d) =>
            d.id === deal.id
              ? { ...d, stage_id: previousStageId, stage_name: deal.stage_name ?? null }
              : d
          )
        );
        setMsg(`Move failed: ${errMessage(e)}`);
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
      setMsg(errMessage(err));
    } finally {
      setSaving(false);
    }
  }

  const columns: Array<{ id: string; name: string; color?: string | null }> = [
    ...stages.map((s) => ({ id: s.id, name: s.name, color: s.color })),
    ...((byStage.get(UNSTAGED)?.length ?? 0) > 0
      ? [{ id: UNSTAGED, name: "Unstaged", color: null }]
      : []),
  ];

  if (loading) {
    return <div className="page"><h1 className="page-title">Deals</h1><LoadingRows rows={5} cols={4} /></div>;
  }

  if (error) {
    return (
      <div className="page">
        <h1 className="page-title">Deals</h1>
        <ErrorState error={error} onRetry={load} />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col p-5 md:p-8">
      <header className="mb-4 flex shrink-0 flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="page-title">Deals</h1>
          <p className="page-sub">
            {visibleDeals.length === deals.length
              ? `${deals.length} opportunities`
              : `${visibleDeals.length} of ${deals.length} shown`} · move work through the deal pipeline; lead lifecycle remains separate.
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-3"><FreshnessNote label="Live CRM board" detail={`${deals.length} loaded`} /><span className="text-[11px] text-ink-faint">Forecast totals use only deal values returned by the API.</span></div>
          <p aria-live="polite" className="mt-1 text-xs text-ink-muted">
            {movingId ? "Saving…" : msg}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button aria-label="Action" type="button" className="btn-primary" onClick={() => setFormOpen(true)}>
            New deal
          </button>
          <button aria-label="Action" type="button" className="btn-secondary" onClick={load}>
            Refresh
          </button>
        </div>
      </header>

      <div className="mb-3 flex shrink-0 flex-wrap items-center gap-2">
        <label className="sr-only" htmlFor="deal-search">
          Search deals
        </label>
        <input
          id="deal-search"
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search deal or company…"
          className="input w-56"
        />
        <label className="sr-only" htmlFor="deal-status-filter">
          Filter by status
        </label>
        <select
          id="deal-status-filter"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="input w-36"
        >
          <option value="">All statuses</option>
          {DEAL_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>

        {(q || statusFilter) && (
          <button aria-label="Action"
            type="button"
            className="btn-secondary"
            onClick={() => {
              setQ("");
              setStatusFilter("");
            }}
          >
            Clear
          </button>
        )}

        <span className="mx-1 h-5 w-px bg-line" aria-hidden="true" />

        {views.map((v) => (
          <span key={v.name} className="inline-flex items-center">
            <button aria-label="Action"
              type="button"
              onClick={() => {
                setQ(v.state.q);
                setStatusFilter(v.state.status);
              }}
              className="pill bg-surface-3 text-ink-muted hover:text-ink"
            >
              {v.name}
            </button>
            <button
              type="button"
              aria-label={`Delete view ${v.name}`}
              onClick={() => setViews(deleteView<DealsView>(VIEW_KEY, v.name))}
              className="ml-0.5 px-1 text-xs text-ink-faint hover:text-rust-500"
            >
              ×
            </button>
          </span>
        ))}

        <button aria-label="Action"
          type="button"
          className="text-xs text-ink-muted underline hover:text-ink"
          disabled={!q && !statusFilter}
          onClick={() => {
            const name = window.prompt("Name this view");
            if (name) setViews(saveView<DealsView>(VIEW_KEY, name, { q, status: statusFilter }));
          }}
        >
          Save view
        </button>
      </div>

      {stages.length === 0 ? (
        <div className="card"><EmptyState title="Deal stages are unavailable" hint="No deal-stage rows were returned for this workspace, so LeadX cannot draw the board yet. Ask an operator to seed the deal pipeline." /></div>
      ) : visibleDeals.length === 0 ? (
        <div className="card"><EmptyState title="No deals match this view" hint="Clear the search or status filter. A missing value or account is shown as unavailable rather than inferred." /></div>
      ) : (
        <div className="flex flex-1 gap-3 overflow-x-auto pb-4">
          {columns.map((col) => {
            const items = byStage.get(col.id) || [];
            const sum = items.reduce((acc, d) => acc + (d.value || 0), 0);
            return (
              <section
                key={col.id}
                onDragOver={(e) => onDragOver(e, col.id)}
                onDragLeave={() => setDragOver(null)}
                onDrop={(e) => onDrop(e, col.id)}
                aria-label={`${col.name} — ${items.length} deals`}
                className={`flex w-72 shrink-0 flex-col rounded-2xl border bg-surface-2/60 transition ${
                  dragOver === col.id ? "border-brand-500 bg-brand-50/40" : "border-line"
                }`}
              >
                <div
                  className="flex items-center justify-between rounded-t-2xl px-3 py-2.5"
                  style={{ borderBottom: `2px solid ${col.color || "rgb(var(--line))"}` }}
                >
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-ink">{col.name}</div>
                    <div className="text-[11px] tabular-nums text-ink-faint">
                      {formatMoney(sum, items[0]?.currency || "USD")}
                    </div>
                  </div>
                  <span className="pill bg-surface-3 tabular-nums text-ink-muted">
                    {items.length}
                  </span>
                </div>

                <div className="max-h-[calc(100vh-240px)] flex-1 space-y-2 overflow-y-auto p-2">
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
                    <p className="rounded-xl border border-dashed border-line py-8 text-center text-xs text-ink-faint">
                      Drop deals here
                    </p>
                  )}
                </div>
              </section>
            );
          })}
        </div>
      )}

      {formOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="new-deal-title"
        >
          <form
            onSubmit={handleCreate}
            className="card-pad max-h-[90vh] w-full max-w-lg space-y-3 overflow-y-auto shadow-lift"
          >
            <div className="flex items-center justify-between">
              <h2 id="new-deal-title" className="text-lg font-semibold text-ink">
                New deal
              </h2>
              <button aria-label="Action" type="button" className="btn-ghost" onClick={() => setFormOpen(false)}>
                Close
              </button>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="sm:col-span-2">
                <label className="label" htmlFor="df-title">
                  Title *
                </label>
                <input
                  id="df-title"
                  className="input mt-1"
                  required
                  value={form.title}
                  onChange={(e) => setForm({ ...form, title: e.target.value })}
                />
              </div>
              <div>
                <label className="label" htmlFor="df-value">
                  Value
                </label>
                <input
                  id="df-value"
                  type="number"
                  min="0"
                  step="0.01"
                  className="input mt-1"
                  value={form.value}
                  onChange={(e) => setForm({ ...form, value: e.target.value })}
                />
              </div>
              <div>
                <label className="label" htmlFor="df-currency">
                  Currency
                </label>
                <input
                  id="df-currency"
                  className="input mt-1"
                  maxLength={3}
                  value={form.currency}
                  onChange={(e) => setForm({ ...form, currency: e.target.value.toUpperCase() })}
                />
              </div>
              <div className="sm:col-span-2">
                <label className="label" htmlFor="df-company">
                  Company
                </label>
                <select
                  id="df-company"
                  className="input mt-1"
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
                <label className="label" htmlFor="df-stage">
                  Stage
                </label>
                <select
                  id="df-stage"
                  className="input mt-1"
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
                <label className="label" htmlFor="df-status">
                  Status
                </label>
                <select
                  id="df-status"
                  className="input mt-1"
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
                <label className="label" htmlFor="df-prob">
                  Probability (%)
                </label>
                <input
                  id="df-prob"
                  type="number"
                  min="0"
                  max="100"
                  className="input mt-1"
                  value={form.probability}
                  onChange={(e) => setForm({ ...form, probability: e.target.value })}
                />
              </div>
              <div>
                <label className="label" htmlFor="df-close">
                  Expected close
                </label>
                <input
                  id="df-close"
                  type="date"
                  className="input mt-1"
                  value={form.expected_close_date}
                  onChange={(e) => setForm({ ...form, expected_close_date: e.target.value })}
                />
              </div>
            </div>

            <div className="flex gap-2">
              <button aria-label="Action" type="submit" disabled={saving} className="btn-primary">
                {saving ? "Saving…" : "Create deal"}
              </button>
              <button aria-label="Action" type="button" className="btn-secondary" onClick={() => setFormOpen(false)}>
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
