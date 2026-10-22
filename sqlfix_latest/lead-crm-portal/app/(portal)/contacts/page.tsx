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
import { FreshnessNote, UnavailableNote } from "@/components/ui/SurfaceMeta";

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

  return (
    <div className="page">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="page-title">Contacts</h1>
          <p className="page-sub">{total} people across your accounts · keep decision-makers, reachability, and ownership visible.</p>
          <div className="mt-2 flex flex-wrap items-center gap-3"><FreshnessNote label="Live CRM view" detail="contact records" /><span className="text-[11px] text-ink-faint">Reachability reflects only email and phone fields returned by the API.</span></div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button aria-label="Action" type="button" onClick={() => setFormOpen(true)} className="btn-primary">
            New contact
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
        <label className="sr-only" htmlFor="contact-search">
          Search contacts
        </label>
        <input
          id="contact-search"
          className="input min-w-[200px] flex-1"
          type="search"
          placeholder="Search name, email, title…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(0);
          }}
        />
        <label className="sr-only" htmlFor="contact-status">
          Filter by status
        </label>
        <select
          id="contact-status"
          className="input max-w-[180px]"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(0);
          }}
        >
          <option value="">All statuses</option>
          {CONTACT_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      {loading ? (
        <LoadingRows rows={6} cols={5} />
      ) : loadError ? (
        // Before this branch existed a failed fetch left rows=[] and the page
        // claimed "No contacts match these filters" — an outage looked like an
        // empty result set.
        <ErrorState error={loadError} onRetry={load} />
      ) : rows.length === 0 ? (
        <EmptyState
          title="No contacts match these filters"
          hint="Clear the filters, or add someone with New contact."
        />
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[820px] text-sm">
            <caption className="sr-only">Contacts with title, email, phone and company</caption>
            <thead>
              <tr className="border-b border-line text-left">
                <th scope="col" className="w-10 px-4 py-3">
                  <input
                    type="checkbox"
                    aria-label={allSelected ? "Deselect all contacts" : "Select all contacts on this page"}
                    checked={allSelected}
                    onChange={toggleAll}
                  />
                </th>
                <th scope="col" className="label px-4 py-3">
                  Name
                </th>
                <th scope="col" className="label px-4 py-3">
                  Title
                </th>
                <th scope="col" className="label px-4 py-3">
                  Email
                </th>
                <th scope="col" className="label px-4 py-3">
                  Phone
                </th>
                <th scope="col" className="label px-4 py-3">Reachability</th>
                <th scope="col" className="label px-4 py-3">Company</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id} className={`border-b border-line/60 hover:bg-surface-3/50 ${changedContactIds.has(String(c.id)) ? "row-flash" : ""}`}>
                  <td className="px-4 py-3">
                    <input
                      type="checkbox"
                      aria-label={`Select ${contactName(c)}`}
                      checked={selected.has(c.id)}
                      onChange={() => toggleOne(c.id)}
                    />
                  </td>
                  <td className="px-4 py-3">
                    <span className="font-medium text-ink">{contactName(c)}</span>
                    <span className="ml-2 inline-flex gap-1">
                      {c.is_primary && (
                        <span className="pill bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-300">
                          Primary
                        </span>
                      )}
                      {c.is_decision_maker && (
                        <span className="pill bg-signal-50 text-signal-700 dark:bg-signal-500/15 dark:text-signal-300">
                          DM
                        </span>
                      )}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-ink-muted">
                    {c.title || <span className="text-ink-faint">—</span>}
                  </td>
                  <td className="px-4 py-3">
                    {c.email ? (
                      <a
                        className="font-medium text-signal-700 hover:underline dark:text-signal-300"
                        href={`mailto:${c.email}`}
                      >
                        {c.email}
                      </a>
                    ) : (
                      <span className="text-ink-faint">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {c.phone ? (
                      <a
                        className="font-medium text-signal-700 hover:underline dark:text-signal-300"
                        href={`tel:${c.phone}`}
                      >
                        {c.phone}
                      </a>
                    ) : (
                      <span className="text-ink-faint">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-ink-muted">
                    {c.email || c.phone ? <span className="pill bg-signal-50 text-signal-700 dark:bg-signal-500/15 dark:text-signal-300">{c.email && c.phone ? "Email + phone" : c.email ? "Email" : "Phone"}</span> : <UnavailableNote label="Reachability" />}
                  </td>
                  <td className="px-4 py-3 text-ink-muted">
                    {companyLabel(c) || <span className="text-ink-faint">—</span>}
                  </td>
                </tr>
              ))}
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
          aria-labelledby="new-contact-title"
        >
          <form
            onSubmit={handleCreate}
            className="card-pad max-h-[90vh] w-full max-w-lg space-y-3 overflow-y-auto shadow-lift"
          >
            <div className="flex items-center justify-between">
              <h2 id="new-contact-title" className="text-lg font-semibold text-ink">
                New contact
              </h2>
              <button aria-label="Action" type="button" className="btn-ghost" onClick={() => setFormOpen(false)}>
                Close
              </button>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="sm:col-span-2">
                <label className="label" htmlFor="ct-name">
                  Full name *
                </label>
                <input
                  id="ct-name"
                  className="input mt-1"
                  required
                  value={form.full_name}
                  onChange={(e) => setForm({ ...form, full_name: e.target.value })}
                />
              </div>
              <div>
                <label className="label" htmlFor="ct-title">
                  Title
                </label>
                <input
                  id="ct-title"
                  className="input mt-1"
                  placeholder="Head of Ops"
                  value={form.title}
                  onChange={(e) => setForm({ ...form, title: e.target.value })}
                />
              </div>
              <div>
                <label className="label" htmlFor="ct-email">
                  Email
                </label>
                <input
                  id="ct-email"
                  type="email"
                  className="input mt-1"
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                />
              </div>
              <div>
                <label className="label" htmlFor="ct-phone">
                  Phone
                </label>
                <input
                  id="ct-phone"
                  className="input mt-1"
                  value={form.phone}
                  onChange={(e) => setForm({ ...form, phone: e.target.value })}
                />
              </div>
              <div>
                <label className="label" htmlFor="ct-status">
                  Status
                </label>
                <select
                  id="ct-status"
                  className="input mt-1"
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
                <label className="label" htmlFor="ct-company">
                  Company
                </label>
                <select
                  id="ct-company"
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
              <div className="flex items-center gap-2">
                <input
                  id="ct-primary"
                  type="checkbox"
                  className="h-4 w-4 rounded border-line"
                  checked={form.is_primary}
                  onChange={(e) => setForm({ ...form, is_primary: e.target.checked })}
                />
                <label className="text-sm text-ink" htmlFor="ct-primary">
                  Primary contact
                </label>
              </div>
              <div className="flex items-center gap-2">
                <input
                  id="ct-dm"
                  type="checkbox"
                  className="h-4 w-4 rounded border-line"
                  checked={form.is_decision_maker}
                  onChange={(e) => setForm({ ...form, is_decision_maker: e.target.checked })}
                />
                <label className="text-sm text-ink" htmlFor="ct-dm">
                  Decision maker
                </label>
              </div>
            </div>

            <div className="flex gap-2">
              <button aria-label="Action" type="submit" disabled={saving} className="btn-primary">
                {saving ? "Saving…" : "Create contact"}
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
