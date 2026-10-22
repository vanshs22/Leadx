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

const PAGE_SIZE = 25;

const STATUS_STYLES: Record<string, string> = {
  active: "bg-signal-50 text-signal-700 dark:bg-signal-500/15 dark:text-signal-300",
  prospect: "bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-300",
  customer: "bg-signal-50 text-signal-700 dark:bg-signal-500/15 dark:text-signal-300",
  churned: "bg-rust-50 text-rust-700 dark:bg-rust-500/15 dark:text-rust-300",
  archived: "bg-surface-3 text-ink-muted",
};

function StatusPill({ status }: { status?: string | null }) {
  if (!status) return <span className="text-ink-faint">—</span>;
  return (
    <span className={`pill ${STATUS_STYLES[status] || "bg-surface-3 text-ink-muted"}`}>
      {status}
    </span>
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

  if (error) return <p className="text-sm text-rust-500 dark:text-rust-500">{error}</p>;
  if (!rows) return <p className="text-sm text-ink-faint">Loading timeline…</p>;
  if (rows.length === 0)
    return <p className="text-sm text-ink-faint">No activity logged for this account yet.</p>;

  return (
    <ol className="space-y-2.5">
      {rows.map((a) => (
        <li key={a.id} className="flex gap-3 text-sm">
          <span className="pill shrink-0 bg-surface-3 text-ink-muted">{a.activity_type}</span>
          <div className="min-w-0">
            <div className="font-medium text-ink">{a.subject || a.outcome || "Activity"}</div>
            {a.body && <p className="mt-0.5 text-ink-muted">{a.body}</p>}
            <div className="mt-0.5 text-xs text-ink-faint">
              {formatDate(a.occurred_at || a.created_at)}
              {a.direction ? ` · ${a.direction}` : ""}
              {a.duration_minutes ? ` · ${a.duration_minutes} min` : ""}
            </div>
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

  return (
    <div className="page">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="page-title">Companies</h1>
          <p className="page-sub">{total} accounts · inspect activity, ownership, and next steps without losing your place.</p>
          <div className="mt-2 flex flex-wrap items-center gap-3"><FreshnessNote label="Live CRM view" detail="account records" /><span className="text-[11px] text-ink-faint">Timeline opens inline; fields not returned by the API stay unavailable.</span></div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button aria-label="Action" type="button" onClick={() => setFormOpen(true)} className="btn-primary">
            New company
          </button>
          <button aria-label="Action" type="button" onClick={load} className="btn-secondary">
            Refresh
          </button>
        </div>
      </header>

      {msg && (
        <div className="rounded-xl border border-line bg-surface-2 px-4 py-2.5 text-sm text-ink-muted">
          {msg}
        </div>
      )}

      <div className="card-pad flex flex-wrap gap-3">
        <label className="sr-only" htmlFor="company-search">
          Search companies
        </label>
        <input
          id="company-search"
          className="input min-w-[200px] flex-1"
          type="search"
          placeholder="Search name, domain, city…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(0);
          }}
        />
        <label className="sr-only" htmlFor="company-status">
          Filter by status
        </label>
        <select
          id="company-status"
          className="input max-w-[180px]"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(0);
          }}
        >
          <option value="">All statuses</option>
          {COMPANY_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      {loading ? (
        <LoadingRows rows={6} cols={5} />
      ) : loadError ? (
        // Distinct from the empty state below: a failed fetch used to leave
        // rows=[] and render "No companies match these filters".
        <ErrorState error={loadError} onRetry={load} />
      ) : rows.length === 0 ? (
        <EmptyState
          title="No companies match these filters"
          hint="Clear the filters, or add your first account with New company."
        />
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <caption className="sr-only">
              Companies with domain, location, industry and status
            </caption>
            <thead>
              <tr className="border-b border-line text-left">
                <th scope="col" className="w-10 px-4 py-3">
                  <input
                    type="checkbox"
                    aria-label={
                      allSelected
                        ? "Deselect all companies"
                        : "Select all companies on this page"
                    }
                    checked={allSelected}
                    onChange={toggleAll}
                  />
                </th>
                <th scope="col" className="label px-4 py-3">
                  Name
                </th>
                <th scope="col" className="label px-4 py-3">
                  Domain
                </th>
                <th scope="col" className="label px-4 py-3">
                  City
                </th>
                <th scope="col" className="label px-4 py-3">
                  Industry
                </th>
                <th scope="col" className="label px-4 py-3">
                  Status
                </th>
                <th scope="col" className="label px-4 py-3 text-right">
                  Timeline
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => {
                const open = expanded === c.id;
                return (
                  <Fragment key={c.id}>
                    <tr className={`border-b border-line/60 hover:bg-surface-3/50 ${changedCompanyIds.has(String(c.id)) ? "row-flash" : ""}`}>
                      <td className="px-4 py-3">
                        <input
                          type="checkbox"
                          aria-label={`Select ${c.name}`}
                          checked={selected.has(c.id)}
                          onChange={() => toggleOne(c.id)}
                        />
                      </td>
                      <td className="px-4 py-3 font-medium text-ink">{c.name}</td>
                      <td className="px-4 py-3 text-ink-muted">
                        {c.domain ? (
                          <a
                            className="hover:underline"
                            href={c.website || `https://${c.domain}`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            {c.domain}
                          </a>
                        ) : (
                          <span className="text-ink-faint">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-ink-muted">
                        {[c.city, c.state].filter(Boolean).join(", ") || (
                          <span className="text-ink-faint">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-ink-muted">
                        {c.industry || <span className="text-ink-faint">—</span>}
                      </td>
                      <td className="px-4 py-3 text-ink-muted">
                        <div className="flex flex-col gap-1">
                          <StatusPill status={c.status} />
                          <EvidenceNote count={Array.isArray(c.tags) ? c.tags.length : undefined} label="tags" />
                        </div>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <button aria-label="Action"
                          type="button"
                          className="btn-ghost text-xs"
                          aria-expanded={open}
                          aria-controls={`timeline-${c.id}`}
                          onClick={() => setExpanded(open ? null : c.id)}
                        >
                          {open ? "Hide" : "Show"}
                        </button>
                      </td>
                    </tr>
                    {open && (
                      <tr className="border-b border-line/60 bg-surface-3/30">
                        <td colSpan={7} className="px-4 py-4" id={`timeline-${c.id}`}>
                          <div className="mb-2 text-sm font-semibold text-ink">
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

      {!loading && rows.length > 0 && (
        <div className="flex items-center justify-between text-sm text-ink-muted">
          <span>
            Page {page + 1} of {pageCount}
          </span>
          <div className="flex gap-2">
            <button aria-label="Action"
              type="button"
              className="btn-secondary"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              Previous
            </button>
            <button aria-label="Action"
              type="button"
              className="btn-secondary"
              disabled={page + 1 >= pageCount}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </button>
          </div>
        </div>
      )}

      {formOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="new-company-title"
        >
          <form
            onSubmit={handleCreate}
            className="card-pad max-h-[90vh] w-full max-w-lg space-y-3 overflow-y-auto shadow-lift"
          >
            <div className="flex items-center justify-between">
              <h2 id="new-company-title" className="text-lg font-semibold text-ink">
                New company
              </h2>
              <button aria-label="Action" type="button" className="btn-ghost" onClick={() => setFormOpen(false)}>
                Close
              </button>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="sm:col-span-2">
                <label className="label" htmlFor="cf-name">
                  Name *
                </label>
                <input
                  id="cf-name"
                  className="input mt-1"
                  required
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </div>
              <div>
                <label className="label" htmlFor="cf-domain">
                  Domain
                </label>
                <input
                  id="cf-domain"
                  className="input mt-1"
                  placeholder="acme.com"
                  value={form.domain}
                  onChange={(e) => setForm({ ...form, domain: e.target.value })}
                />
              </div>
              <div>
                <label className="label" htmlFor="cf-website">
                  Website
                </label>
                <input
                  id="cf-website"
                  className="input mt-1"
                  placeholder="https://acme.com"
                  value={form.website}
                  onChange={(e) => setForm({ ...form, website: e.target.value })}
                />
              </div>
              <div>
                <label className="label" htmlFor="cf-phone">
                  Phone
                </label>
                <input
                  id="cf-phone"
                  className="input mt-1"
                  value={form.phone}
                  onChange={(e) => setForm({ ...form, phone: e.target.value })}
                />
              </div>
              <div>
                <label className="label" htmlFor="cf-industry">
                  Industry
                </label>
                <input
                  id="cf-industry"
                  className="input mt-1"
                  value={form.industry}
                  onChange={(e) => setForm({ ...form, industry: e.target.value })}
                />
              </div>
              <div>
                <label className="label" htmlFor="cf-city">
                  City
                </label>
                <input
                  id="cf-city"
                  className="input mt-1"
                  value={form.city}
                  onChange={(e) => setForm({ ...form, city: e.target.value })}
                />
              </div>
              <div>
                <label className="label" htmlFor="cf-state">
                  State
                </label>
                <input
                  id="cf-state"
                  className="input mt-1"
                  value={form.state}
                  onChange={(e) => setForm({ ...form, state: e.target.value })}
                />
              </div>
              <div>
                <label className="label" htmlFor="cf-status">
                  Status
                </label>
                <select
                  id="cf-status"
                  className="input mt-1"
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
                <label className="label" htmlFor="cf-owner">
                  Owner
                </label>
                <input
                  id="cf-owner"
                  className="input mt-1"
                  placeholder="rep@agency.com"
                  value={form.owner}
                  onChange={(e) => setForm({ ...form, owner: e.target.value })}
                />
              </div>
              <div className="sm:col-span-2">
                <label className="label" htmlFor="cf-tags">
                  Tags (comma separated)
                </label>
                <input
                  id="cf-tags"
                  className="input mt-1"
                  placeholder="enterprise, inbound"
                  value={form.tags}
                  onChange={(e) => setForm({ ...form, tags: e.target.value })}
                />
              </div>
            </div>

            <div className="flex gap-2">
              <button aria-label="Action" type="submit" disabled={saving} className="btn-primary">
                {saving ? "Saving…" : "Create company"}
              </button>
              <button aria-label="Action" type="button" className="btn-secondary" onClick={() => setFormOpen(false)}>
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

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
