"use client";

import { useCallback, useEffect, useState, useRef } from "react";
import {
  crmEntities,
  CONTACT_STATUSES,
  contactName,
  errMessage,
  type Company,
  type Contact,
} from "@/lib/crm";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { useChangedIds } from "@/lib/live";
import { BulkActionBar } from "@/components/BulkActionBar";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/States";
import { FreshnessNote } from "@/components/ui/SurfaceMeta";
import { PageHeader, FilterBar, Pagination, Panel, Badge, StatusBadge, Drawer } from "@/components/ui/Primitives";
import { Icon } from "@/components/ui/Icon";

const PAGE_SIZE = 25;

export default function ContactsPage() {
  const tenantId = getTenantId();
  const [rows, setRows] = useState<Contact[]>([]);
  const [total, setTotal] = useState(0);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [loading, setLoading] = useState(true);
  const changedContactIds = useChangedIds(rows, (r: any) => String(r.id));
  const loadedOnceRef = useRef(false);
  const [msg, setMsg] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(0);

  const [formOpen, setFormOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const emptyForm = {
    full_name: "",
    title: "",
    email: "",
    phone: "",
    company_id: "",
    status: "active",
    is_primary: false,
    is_decision_maker: false,
  };
  const [form, setForm] = useState(emptyForm);

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

  /** Runs one API call per selected row and reports partial failure rather
   *  than silently stopping at the first error. */
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
        : `${verb} ${ids.length} contact${ids.length === 1 ? "" : "s"}.`
    );
    load();
  }

  const load = useCallback(() => {
    if (!tenantId) {
      setLoading(false);
      setMsg("No workspace selected.");
      return;
    }
    setLoading(true);
    setLoadError(null);
    crmEntities
      .listContacts(tenantId, {
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

  useCrmChange(load, { tables: ["contacts", "contact_roles", "activities"] });

  // Company list powers the "Company" dropdown in the create form and lets us
  // show a company name when the backend doesn't join one.
  useEffect(() => {
    if (!tenantId) return;
    let cancelled = false;
    crmEntities
      .listCompanies(tenantId, { limit: 200 })
      .then((r) => {
        if (!cancelled) setCompanies(r.rows);
      })
      .catch(() => {
        /* non-fatal: the dropdown just stays empty */
      });
    return () => {
      cancelled = true;
    };
  }, [tenantId]);

  function companyLabel(c: Contact): string {
    if (c.company_name) return c.company_name;
    const match = companies.find((co) => co.id === c.company_id);
    return match?.name || "";
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!form.full_name.trim()) {
      setMsg("Contact name is required");
      return;
    }
    setSaving(true);
    setMsg("");
    try {
      await crmEntities.createContact(tenantId, {
        full_name: form.full_name.trim(),
        title: form.title.trim() || null,
        email: form.email.trim() || null,
        phone: form.phone.trim() || null,
        company_id: form.company_id || null,
        status: form.status,
        is_primary: form.is_primary,
        is_decision_maker: form.is_decision_maker,
      });
      setMsg(`Created ${form.full_name.trim()}`);
      setFormOpen(false);
      setForm(emptyForm);
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

  return (
    <div className="page">
      <PageHeader
        eyebrow="CRM"
        title="Contacts"
        description={<><span className="tabular-nums">{total.toLocaleString()}</span> people across your accounts. Keep decision-makers, reachability and ownership visible.</>}
        meta={<><FreshnessNote label="Live CRM view" detail="contact records" /><span>Reachability reflects only email and phone fields returned by the API.</span></>}
        actions={<>
          <button aria-label="Refresh contacts" type="button" onClick={load} className="btn-secondary"><Icon name="refresh" size={14} />Refresh</button>
          <button aria-label="Create contact" type="button" onClick={() => setFormOpen(true)} className="btn-primary"><Icon name="plus" size={14} />New contact</button>
        </>}
      />

      {msg && (
        <div role="status" className="notice notice-neutral items-center">
          <span className="min-w-0">{msg}</span>
          <button type="button" className="btn-ghost btn-sm -mr-2 px-2" aria-label="Dismiss message" onClick={() => setMsg("")}><Icon name="close" size={14} /></button>
        </div>
      )}

      <FilterBar onClear={() => { setSearch(""); setStatus(""); setPage(0); }}>
        <label className="sr-only" htmlFor="contact-search">Search contacts</label>
        <div className="relative min-w-[220px] flex-1">
          <Icon name="search" size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint" />
          <input id="contact-search" className="input h-9 pl-8" type="search" placeholder="Search name, email, title…" value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} />
        </div>
        <label className="sr-only" htmlFor="contact-status">Filter by status</label>
        <select id="contact-status" className="select h-9 w-auto min-w-[160px] capitalize" value={status} onChange={(e) => { setStatus(e.target.value); setPage(0); }}><option value="">All statuses</option>{CONTACT_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}</select>
      </FilterBar>

      <Panel
        title="People"
        actions={<span className="text-xs tabular-nums text-ink-faint" aria-live="polite">{selected.size ? `${selected.size} selected · ` : ""}{rows.length} of {total.toLocaleString()} shown</span>}
        bodyClassName="p-0"
        footer={!loading && rows.length > 0 && pageCount > 1 ? <div className="w-full"><Pagination page={page} pageCount={pageCount} onPageChange={setPage} /></div> : undefined}
      >
        {loading ? (
          <LoadingRows rows={6} cols={5} />
        ) : loadError ? (
          // Before this branch existed a failed fetch left rows=[] and the page
          // claimed "No contacts match these filters" — an outage looked like an
          // empty result set.
          <div className="p-5"><ErrorState error={loadError} onRetry={load} /></div>
        ) : rows.length === 0 ? (
          <EmptyState
            title="No contacts match these filters"
            hint="Clear the filters, or add someone with New contact."
          />
        ) : (
          <div className="max-h-[calc(100vh-18rem)] min-h-[280px] overflow-auto">
            <table className="data-table min-w-[960px]">
              <caption className="sr-only">Contacts with title, email, phone and company</caption>
              <thead>
                <tr className="[&>th]:bg-surface-3">
                  <th scope="col" className="w-11 !pr-0">
                    <input
                      type="checkbox"
                      className="h-4 w-4 rounded border-line-strong accent-brand-700"
                      aria-label={allSelected ? "Deselect all contacts" : "Select all contacts on this page"}
                      checked={allSelected}
                      onChange={toggleAll}
                    />
                  </th>
                  <th scope="col">Name</th>
                  <th scope="col">Title</th>
                  <th scope="col">Email</th>
                  <th scope="col">Phone</th>
                  <th scope="col">Reachability</th>
                  <th scope="col">Company</th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => {
                  const name = contactName(c);
                  const company = companyLabel(c);
                  const isSelected = selected.has(c.id);
                  return (
                    <tr key={c.id} className={`${isSelected ? "bg-brand-50/70 dark:bg-brand-500/10" : ""} ${changedContactIds.has(String(c.id)) ? "row-flash" : ""}`}>
                      <td className="!pr-0 !py-2.5">
                        <input
                          type="checkbox"
                          className="h-4 w-4 rounded border-line-strong accent-brand-700"
                          aria-label={`Select ${name}`}
                          checked={isSelected}
                          onChange={() => toggleOne(c.id)}
                        />
                      </td>
                      <td className="max-w-[260px] !py-2.5">
                        <div className="flex min-w-0 items-center gap-1.5">
                          <span className="truncate font-semibold text-ink" title={name}>{name}</span>
                          {c.is_primary && <Badge tone="brand" className="shrink-0">Primary</Badge>}
                          {c.is_decision_maker && <Badge tone="neutral" className="shrink-0"><span title="Decision maker">DM</span></Badge>}
                        </div>
                      </td>
                      <td className="max-w-[180px] !py-2.5 text-ink-muted">
                        {c.title ? <span className="block truncate" title={c.title}>{c.title}</span> : <span className="text-ink-faint">—</span>}
                      </td>
                      <td className="max-w-[220px] !py-2.5">
                        {c.email ? (
                          <a
                            className="block truncate text-brand-700 hover:underline dark:text-brand-300"
                            href={`mailto:${c.email}`}
                            title={c.email}
                          >
                            {c.email}
                          </a>
                        ) : (
                          <span className="text-ink-faint">—</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap !py-2.5">
                        {c.phone ? (
                          <a
                            className="tabular-nums text-brand-700 hover:underline dark:text-brand-300"
                            href={`tel:${c.phone}`}
                          >
                            {c.phone}
                          </a>
                        ) : (
                          <span className="text-ink-faint">—</span>
                        )}
                      </td>
                      <td className="!py-2.5">
                        {c.email || c.phone ? (
                          <Badge tone={c.email && c.phone ? "success" : "neutral"}>{c.email && c.phone ? "Email + phone" : c.email ? "Email" : "Phone"}</Badge>
                        ) : (
                          <Badge tone="warn">Reachability unavailable</Badge>
                        )}
                      </td>
                      <td className="max-w-[200px] !py-2.5 text-ink-muted">
                        {company ? <span className="block truncate" title={company}>{company}</span> : <span className="text-ink-faint">—</span>}
                      </td>
                      <td className="!py-2.5">{c.status ? <StatusBadge status={c.status} /> : <span className="text-ink-faint">—</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Drawer
        open={formOpen}
        title="New contact"
        onClose={closeForm}
        footer={
          <div className="flex justify-end gap-2">
            <button type="button" className="btn-secondary" onClick={() => setFormOpen(false)}>
              Cancel
            </button>
            <button type="submit" form="new-contact-form" disabled={saving} className="btn-primary">
              {saving ? "Saving…" : "Create contact"}
            </button>
          </div>
        }
      >
        <form id="new-contact-form" onSubmit={handleCreate} aria-labelledby="new-contact-title" className="space-y-4">
          <p id="new-contact-title" className="section-sub !mt-0">Add a person to the CRM. Only the full name is required.</p>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <label className="field-label" htmlFor="ct-name">
                Full name <span className="text-rust-600 dark:text-rust-300" aria-hidden="true">*</span>
              </label>
              <input
                id="ct-name"
                className="input"
                required
                aria-required="true"
                value={form.full_name}
                onChange={(e) => setForm({ ...form, full_name: e.target.value })}
              />
            </div>
            <div>
              <label className="field-label" htmlFor="ct-title">
                Title
              </label>
              <input
                id="ct-title"
                className="input"
                placeholder="Head of Ops"
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
              />
            </div>
            <div>
              <label className="field-label" htmlFor="ct-email">
                Email
              </label>
              <input
                id="ct-email"
                type="email"
                className="input"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
              />
            </div>
            <div>
              <label className="field-label" htmlFor="ct-phone">
                Phone
              </label>
              <input
                id="ct-phone"
                className="input"
                value={form.phone}
                onChange={(e) => setForm({ ...form, phone: e.target.value })}
              />
            </div>
            <div>
              <label className="field-label" htmlFor="ct-status">
                Status
              </label>
              <select
                id="ct-status"
                className="select capitalize"
                value={form.status}
                onChange={(e) => setForm({ ...form, status: e.target.value })}
              >
                {CONTACT_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
            <div className="sm:col-span-2">
              <label className="field-label" htmlFor="ct-company">
                Company
              </label>
              <select
                id="ct-company"
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
            <div className="flex items-center gap-2">
              <input
                id="ct-primary"
                type="checkbox"
                className="h-4 w-4 rounded border-line-strong accent-brand-700"
                checked={form.is_primary}
                onChange={(e) => setForm({ ...form, is_primary: e.target.checked })}
              />
              <label className="text-[0.8125rem] text-ink" htmlFor="ct-primary">
                Primary contact
              </label>
            </div>
            <div className="flex items-center gap-2">
              <input
                id="ct-dm"
                type="checkbox"
                className="h-4 w-4 rounded border-line-strong accent-brand-700"
                checked={form.is_decision_maker}
                onChange={(e) => setForm({ ...form, is_decision_maker: e.target.checked })}
              />
              <label className="text-[0.8125rem] text-ink" htmlFor="ct-dm">
                Decision maker
              </label>
            </div>
          </div>
        </form>
      </Drawer>

      <BulkActionBar
        count={selected.size}
        noun={`contact${selected.size === 1 ? "" : "s"} selected`}
        onClear={() => setSelected(new Set())}
        actions={[
          {
            label: "Mark decision-maker",
            disabled: bulkBusy,
            onClick: () =>
              bulkApply("Updated", (id) =>
                crmEntities.updateContact(tenantId, id, { is_decision_maker: true })
              ),
          },
          {
            label: "Delete",
            disabled: bulkBusy,
            onClick: () =>
              bulkApply(
                "Deleted",
                (id) => crmEntities.deleteContact(tenantId, id),
                `Delete ${selected.size} contact${selected.size === 1 ? "" : "s"}? This cannot be undone.`
              ),
          },
        ]}
      />
    </div>
  );
}
