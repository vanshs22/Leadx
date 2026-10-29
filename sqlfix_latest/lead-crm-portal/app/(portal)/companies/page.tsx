"use client";

import { Fragment, useCallback, useEffect, useState, useRef } from "react";
import {
  crmEntities,
  COMPANY_STATUSES,
  errMessage,
  formatDate,
  type Activity,
  type Company,
} from "@/lib/crm";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { useChangedIds } from "@/lib/live";
import { BulkActionBar } from "@/components/BulkActionBar";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/States";
import { EvidenceNote, FreshnessNote } from "@/components/ui/SurfaceMeta";
import { PageHeader, FilterBar, Pagination, Panel, Badge, Drawer, type Tone } from "@/components/ui/Primitives";
import { Icon } from "@/components/ui/Icon";

const PAGE_SIZE = 25;

/* Account lifecycle statuses are CRM-specific, so they get their own tone map. */
const STATUS_TONES: Record<string, Tone> = {
  active: "success",
  prospect: "brand",
  customer: "success",
  churned: "danger",
  archived: "neutral",
};

function StatusPill({ status }: { status?: string | null }) {
  if (!status) return <span className="text-ink-faint">—</span>;
  const tone = STATUS_TONES[status] ?? "neutral";
  const dot = tone === "success" ? "bg-emerald-600 dark:bg-emerald-400" : tone === "brand" ? "bg-brand-600 dark:bg-brand-300" : tone === "danger" ? "bg-rust-500" : "bg-line-strong";
  return (
    <Badge tone={tone} className="capitalize">
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} aria-hidden="true" />
      {status}
    </Badge>
  );
}

/** Expanded row: activity timeline for one company. */
function CompanyTimeline({ tenantId, companyId }: { tenantId: string; companyId: string }) {
  const [rows, setRows] = useState<Activity[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    crmEntities
      .companyTimeline(tenantId, companyId)
      .then((r) => {
        if (!cancelled) setRows(r);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(errMessage(e));
      });
    return () => {
      cancelled = true;
    };
  }, [tenantId, companyId]);

  if (error) return <p role="alert" className="text-[0.8125rem] text-rust-600 dark:text-rust-300">{error}</p>;
  if (!rows) return <p className="text-[0.8125rem] text-ink-faint" aria-busy="true">Loading timeline…</p>;
  if (rows.length === 0)
    return <p className="text-[0.8125rem] text-ink-faint">No activity logged for this account yet.</p>;

  return (
    <ol className="relative space-y-3 border-l border-line pl-4">
      {rows.map((a) => (
        <li key={a.id} className="relative text-[0.8125rem]">
          <span className="absolute -left-[21px] top-1.5 h-2 w-2 rounded-full border-2 border-surface-2 bg-brand-600 dark:bg-brand-300" aria-hidden="true" />
          <div className="flex flex-wrap items-center gap-2">
            <span className="badge-neutral capitalize">{a.activity_type}</span>
            <span className="font-medium text-ink">{a.subject || a.outcome || "Activity"}</span>
          </div>
          {a.body && <p className="mt-1 leading-5 text-ink-muted">{a.body}</p>}
          <div className="mt-0.5 text-xs tabular-nums text-ink-faint">
            {formatDate(a.occurred_at || a.created_at)}
            {a.direction ? ` · ${a.direction}` : ""}
            {a.duration_minutes ? ` · ${a.duration_minutes} min` : ""}
          </div>
        </li>
      ))}
    </ol>
  );
}

export default function CompaniesPage() {
  const tenantId = getTenantId();
  const [rows, setRows] = useState<Company[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const changedCompanyIds = useChangedIds(rows, (r: any) => String(r.id));
  const loadedOnceRef = useRef(false);
  const [msg, setMsg] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(0);
  const [expanded, setExpanded] = useState<string | null>(null);

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [loadError, setLoadError] = useState<unknown>(null);
  const allSelected = rows.length > 0 && rows.every((r) => selected.has(r.id));

  function toggleOne(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(rows.map((r) => r.id)));
  }

  /** One API call per selected row; reports partial failure instead of
   *  stopping silently at the first error. */
  async function bulkApply(
    verb: string,
    fn: (id: string) => Promise<unknown>,
    confirmMsg?: string
  ) {
    if (!tenantId || selected.size === 0) return;
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    setBulkBusy(true);
    const ids = [...selected];
    const results = await Promise.allSettled(ids.map(fn));
    const failed = results.filter((r) => r.status === "rejected").length;
    setBulkBusy(false);
    setSelected(new Set());
    setMsg(
      failed
        ? `${verb} ${ids.length - failed} of ${ids.length} — ${failed} failed.`
        : `${verb} ${ids.length} compan${ids.length === 1 ? "y" : "ies"}.`
    );
    load();
  }

  const [formOpen, setFormOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    name: "",
    domain: "",
    website: "",
    phone: "",
    city: "",
    state: "",
    industry: "",
    status: "prospect",
    owner: "",
    tags: "",
  });

  const load = useCallback(() => {
    if (!tenantId) {
      setLoading(false);
      setMsg("No workspace selected.");
      return;
    }
    setLoading(true);
    setLoadError(null);
    crmEntities
      .listCompanies(tenantId, {
        search: search || undefined,
        status: status || undefined,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      })
      .then((r) => {
        setRows(r.rows);
        setTotal(r.total);
      })
      .catch((e: unknown) => setLoadError(e))
      .finally(() => { loadedOnceRef.current = true; setLoading(false); });
  }, [tenantId, search, status, page]);

  useEffect(() => {
    load();
  }, [load]);

  useCrmChange(load, { tables: ["companies", "company_locations", "activities"] });

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!form.name.trim()) {
      setMsg("Company name is required");
      return;
    }
    setSaving(true);
    setMsg("");
    try {
      await crmEntities.createCompany(tenantId, {
        name: form.name.trim(),
        domain: form.domain.trim() || null,
        website: form.website.trim() || null,
        phone: form.phone.trim() || null,
        city: form.city.trim() || null,
        state: form.state.trim() || null,
        industry: form.industry.trim() || null,
        status: form.status,
        owner: form.owner.trim() || null,
        tags: form.tags
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
      });
      setMsg(`Created ${form.name.trim()}`);
      setFormOpen(false);
      setForm({
        name: "",
        domain: "",
        website: "",
        phone: "",
        city: "",
        state: "",
        industry: "",
        status: "prospect",
        owner: "",
        tags: "",
      });
      setPage(0);
      load();
    } catch (err: unknown) {
      setMsg(errMessage(err));
    } finally {
      setSaving(false);
    }
  }

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const closeForm = useCallback(() => setFormOpen(false), []);
  const fieldCls = "input";

  return (
    <div className="page">
      <PageHeader
        eyebrow="CRM"
        title="Companies"
        description={<><span className="tabular-nums">{total.toLocaleString()}</span> accounts. Inspect activity, ownership and next steps without losing your place.</>}
        meta={<><FreshnessNote label="Live CRM view" detail="account records" /><span>Timeline opens inline; fields not returned by the API stay unavailable.</span></>}
        actions={<>
          <button aria-label="Refresh companies" type="button" onClick={load} className="btn-secondary"><Icon name="refresh" size={14} />Refresh</button>
          <button aria-label="Create company" type="button" onClick={() => setFormOpen(true)} className="btn-primary"><Icon name="plus" size={14} />New company</button>
        </>}
      />

      {msg && (
        <div role="status" className="notice notice-neutral items-center">
          <span className="min-w-0">{msg}</span>
          <button type="button" className="btn-ghost btn-sm -mr-2 px-2" aria-label="Dismiss message" onClick={() => setMsg("")}><Icon name="close" size={14} /></button>
        </div>
      )}

      <FilterBar onClear={() => { setSearch(""); setStatus(""); setPage(0); }}>
        <label className="sr-only" htmlFor="company-search">Search companies</label>
        <div className="relative min-w-[220px] flex-1">
          <Icon name="search" size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint" />
          <input id="company-search" className="input h-9 pl-8" type="search" placeholder="Search name, domain, city…" value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} />
        </div>
        <label className="sr-only" htmlFor="company-status">Filter by status</label>
        <select id="company-status" className="select h-9 w-auto min-w-[160px] capitalize" value={status} onChange={(e) => { setStatus(e.target.value); setPage(0); }}><option value="">All statuses</option>{COMPANY_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}</select>
      </FilterBar>

      <Panel
        title="Accounts"
        actions={<span className="text-xs tabular-nums text-ink-faint" aria-live="polite">{selected.size ? `${selected.size} selected · ` : ""}{rows.length} of {total.toLocaleString()} shown</span>}
        bodyClassName="p-0"
        footer={!loading && rows.length > 0 && pageCount > 1 ? <div className="w-full"><Pagination page={page} pageCount={pageCount} onPageChange={setPage} /></div> : undefined}
      >
        {loading ? (
          <LoadingRows rows={6} cols={5} />
        ) : loadError ? (
          // Distinct from the empty state below: a failed fetch used to leave
          // rows=[] and render "No companies match these filters".
          <div className="p-5"><ErrorState error={loadError} onRetry={load} /></div>
        ) : rows.length === 0 ? (
          <EmptyState
            title="No companies match these filters"
            hint="Clear the filters, or add your first account with New company."
          />
        ) : (
          <div className="max-h-[calc(100vh-18rem)] min-h-[280px] overflow-auto">
            <table className="data-table min-w-[860px]">
              <caption className="sr-only">
                Companies with domain, location, industry and status
              </caption>
              <thead>
                <tr className="[&>th]:bg-surface-3">
                  <th scope="col" className="w-11 !pr-0">
                    <input
                      type="checkbox"
                      className="h-4 w-4 rounded border-line-strong accent-brand-700"
                      aria-label={
                        allSelected
                          ? "Deselect all companies"
                          : "Select all companies on this page"
                      }
                      checked={allSelected}
                      onChange={toggleAll}
                    />
                  </th>
                  <th scope="col">Name</th>
                  <th scope="col">Domain</th>
                  <th scope="col">City</th>
                  <th scope="col">Industry</th>
                  <th scope="col">Status</th>
                  <th scope="col" className="!text-right">Tags</th>
                  <th scope="col" className="!text-right">Timeline</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => {
                  const open = expanded === c.id;
                  const isSelected = selected.has(c.id);
                  const place = [c.city, c.state].filter(Boolean).join(", ");
                  return (
                    <Fragment key={c.id}>
                      <tr className={`${isSelected ? "bg-brand-50/70 dark:bg-brand-500/10" : ""} ${open ? "bg-surface-3/40" : ""} ${changedCompanyIds.has(String(c.id)) ? "row-flash" : ""}`}>
                        <td className="!pr-0 !py-2.5">
                          <input
                            type="checkbox"
                            className="h-4 w-4 rounded border-line-strong accent-brand-700"
                            aria-label={`Select ${c.name}`}
                            checked={isSelected}
                            onChange={() => toggleOne(c.id)}
                          />
                        </td>
                        <td className="max-w-[260px] !py-2.5"><span className="block truncate font-semibold text-ink" title={c.name}>{c.name}</span></td>
                        <td className="max-w-[200px] !py-2.5 text-ink-muted">
                          {c.domain ? (
                            <a
                              className="inline-flex max-w-full items-center gap-1 truncate text-brand-700 hover:underline dark:text-brand-300"
                              href={c.website || `https://${c.domain}`}
                              target="_blank"
                              rel="noreferrer"
                              title={c.website || c.domain}
                            >
                              <span className="truncate">{c.domain}</span>
                              <Icon name="external" size={12} className="shrink-0 opacity-60" />
                            </a>
                          ) : (
                            <span className="text-ink-faint">—</span>
                          )}
                        </td>
                        <td className="max-w-[180px] !py-2.5 text-ink-muted">
                          {place ? <span className="block truncate" title={place}>{place}</span> : <span className="text-ink-faint">—</span>}
                        </td>
                        <td className="max-w-[180px] !py-2.5 text-ink-muted">
                          {c.industry ? <span className="block truncate" title={c.industry}>{c.industry}</span> : <span className="text-ink-faint">—</span>}
                        </td>
                        <td className="!py-2.5"><StatusPill status={c.status} /></td>
                        <td className="!py-2.5 text-right tabular-nums">
                          <EvidenceNote count={Array.isArray(c.tags) ? c.tags.length : undefined} label="tags" />
                        </td>
                        <td className="!py-2.5 text-right">
                          <button
                            type="button"
                            className="btn-ghost btn-sm"
                            aria-expanded={open}
                            aria-controls={`timeline-${c.id}`}
                            aria-label={`${open ? "Hide" : "Show"} activity timeline for ${c.name}`}
                            onClick={() => setExpanded(open ? null : c.id)}
                          >
                            {open ? "Hide" : "Show"}
                            <Icon name="chevron" size={12} className={`transition-transform ${open ? "rotate-90" : ""}`} />
                          </button>
                        </td>
                      </tr>
                      {open && (
                        <tr className="bg-surface-3/40">
                          <td colSpan={8} className="!px-5 !py-4" id={`timeline-${c.id}`}>
                            <div className="mb-3 section-title">
                              {c.name} · activity
                            </div>
                            <CompanyTimeline tenantId={tenantId} companyId={c.id} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Drawer
        open={formOpen}
        title="New company"
        onClose={closeForm}
        footer={
          <div className="flex justify-end gap-2">
            <button type="button" className="btn-secondary" onClick={() => setFormOpen(false)}>
              Cancel
            </button>
            <button type="submit" form="new-company-form" disabled={saving} className="btn-primary">
              {saving ? "Saving…" : "Create company"}
            </button>
          </div>
        }
      >
        <form id="new-company-form" onSubmit={handleCreate} aria-labelledby="new-company-title" className="space-y-4">
          <p id="new-company-title" className="section-sub !mt-0">Add an account to the CRM. Only the name is required.</p>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <label className="field-label" htmlFor="cf-name">
                Name <span className="text-rust-600 dark:text-rust-300" aria-hidden="true">*</span>
              </label>
              <input
                id="cf-name"
                className={fieldCls}
                required
                aria-required="true"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </div>
            <div>
              <label className="field-label" htmlFor="cf-domain">
                Domain
              </label>
              <input
                id="cf-domain"
                className={fieldCls}
                placeholder="acme.com"
                value={form.domain}
                onChange={(e) => setForm({ ...form, domain: e.target.value })}
              />
            </div>
            <div>
              <label className="field-label" htmlFor="cf-website">
                Website
              </label>
              <input
                id="cf-website"
                className={fieldCls}
                placeholder="https://acme.com"
                value={form.website}
                onChange={(e) => setForm({ ...form, website: e.target.value })}
              />
            </div>
            <div>
              <label className="field-label" htmlFor="cf-phone">
                Phone
              </label>
              <input
                id="cf-phone"
                className={fieldCls}
                value={form.phone}
                onChange={(e) => setForm({ ...form, phone: e.target.value })}
              />
            </div>
            <div>
              <label className="field-label" htmlFor="cf-industry">
                Industry
              </label>
              <input
                id="cf-industry"
                className={fieldCls}
                value={form.industry}
                onChange={(e) => setForm({ ...form, industry: e.target.value })}
              />
            </div>
            <div>
              <label className="field-label" htmlFor="cf-city">
                City
              </label>
              <input
                id="cf-city"
                className={fieldCls}
                value={form.city}
                onChange={(e) => setForm({ ...form, city: e.target.value })}
              />
            </div>
            <div>
              <label className="field-label" htmlFor="cf-state">
                State
              </label>
              <input
                id="cf-state"
                className={fieldCls}
                value={form.state}
                onChange={(e) => setForm({ ...form, state: e.target.value })}
              />
            </div>
            <div>
              <label className="field-label" htmlFor="cf-status">
                Status
              </label>
              <select
                id="cf-status"
                className="select capitalize"
                value={form.status}
                onChange={(e) => setForm({ ...form, status: e.target.value })}
              >
                {COMPANY_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="field-label" htmlFor="cf-owner">
                Owner
              </label>
              <input
                id="cf-owner"
                className={fieldCls}
                placeholder="rep@agency.com"
                value={form.owner}
                onChange={(e) => setForm({ ...form, owner: e.target.value })}
              />
            </div>
            <div className="sm:col-span-2">
              <label className="field-label" htmlFor="cf-tags">
                Tags
              </label>
              <input
                id="cf-tags"
                className={fieldCls}
                placeholder="enterprise, inbound"
                aria-describedby="cf-tags-hint"
                value={form.tags}
                onChange={(e) => setForm({ ...form, tags: e.target.value })}
              />
              <p id="cf-tags-hint" className="field-hint">Comma separated.</p>
            </div>
          </div>
        </form>
      </Drawer>

      <BulkActionBar
        count={selected.size}
        noun={`compan${selected.size === 1 ? "y" : "ies"} selected`}
        onClear={() => setSelected(new Set())}
        actions={[
          {
            label: "Set status…",
            disabled: bulkBusy,
            onClick: () => {
              const next = window.prompt(
                `Set status to one of: ${COMPANY_STATUSES.join(", ")}`
              );
              const clean = (next || "").trim();
              if (!clean) return;
              if (!COMPANY_STATUSES.includes(clean as (typeof COMPANY_STATUSES)[number])) {
                setMsg(`"${clean}" is not a valid status.`);
                return;
              }
              bulkApply("Updated", (id) =>
                crmEntities.updateCompany(tenantId, id, { status: clean })
              );
            },
          },
          {
            label: "Delete",
            disabled: bulkBusy,
            onClick: () =>
              bulkApply(
                "Deleted",
                (id) => crmEntities.deleteCompany(tenantId, id),
                `Delete ${selected.size} compan${selected.size === 1 ? "y" : "ies"}? Their contacts and deals may be affected. This cannot be undone.`
              ),
          },
        ]}
      />
    </div>
  );
}
