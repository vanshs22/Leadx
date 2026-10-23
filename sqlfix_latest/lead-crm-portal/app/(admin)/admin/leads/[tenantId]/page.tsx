"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { admin } from "@/lib/admin";

type LeadRow = {
  assignment_id?: string;
  lead_id?: string;
  name?: string;
  phone?: string;
  city?: string;
  state?: string;
  website?: string;
  source?: string;
  tier?: string;
  status?: string;
  delivery_status?: string;
  display_status?: string;
  delivery_channel?: string;
  score?: number;
  assigned_at?: string;
  delivered_at?: string;
  category?: string;
};

type TierSort = "default" | "A_first" | "C_first";
const TIER_ORDER: Record<string, number> = { A: 0, B: 1, C: 2 };

function tierBadge(tier?: string) {
  const t = (tier || "?").toUpperCase();
  const cls =
    t === "A"
      ? "bg-signal-500/15 text-signal-300 ring-signal-500/30"
      : t === "B"
        ? "bg-brand-500/15 text-brand-300 ring-brand-500/30"
        : t === "C"
          ? "bg-surface-3 text-ink-muted ring-line"
          : "bg-surface-3 text-ink-muted ring-line";
  return (
    <span className={`inline-flex min-w-[1.75rem] items-center justify-center rounded-md px-2 py-0.5 text-xs font-semibold ring-1 ${cls}`}>
      {t}
    </span>
  );
}

function statusBadge(status?: string) {
  const s = (status || "—").toLowerCase();
  const label =
    s === "pending" ? "Pending"
    : s === "new" || s === "assigned" ? "Assigned"
    : s === "portal" ? "In portal"
    : s === "sent" || s === "delivered" ? "Delivered"
    : s === "failed" ? "Assigned"
    : s === "won" ? "Won"
    : s === "rejected" || s === "expired" ? s.charAt(0).toUpperCase() + s.slice(1)
    : (status || "—");
  const cls =
    s === "sent" || s === "new" || s === "delivered" || s === "pending" || s === "assigned" || s === "portal" || s === "failed"
      ? "bg-signal-500/10 text-signal-300"
      : s === "won"
        ? "bg-signal-500/10 text-signal-300"
        : s === "failed" || s === "rejected" || s === "expired"
          ? "bg-rust-500/10 text-rust-300"
          : "bg-surface-3 text-ink-muted";
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${cls}`}>
      {label}
    </span>
  );
}

export default function AdminClientLeadsPage() {
  const params = useParams();
  const tenantId = String(params?.tenantId || params?.id || "");
  const [leads, setLeads] = useState<LeadRow[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [clientName, setClientName] = useState("");
  const [tierSort, setTierSort] = useState<TierSort>("default");
  const [q, setQ] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const [showAdd, setShowAdd] = useState(false);
  const [addForm, setAddForm] = useState({
    name: "", phone: "", email: "", website: "", address: "", city: "", state: "",
    category: "", tier: "B", owner_name: "", pitch_line: "", linkedin_url: "", instagram_handle: "",
  });
  const [adding, setAdding] = useState(false);

  const load = useCallback(() => {
    if (!tenantId) return;
    setLoading(true);
    setError("");
    Promise.all([
      admin.clientLeads(tenantId, 300, 0),
      admin.tenants().catch(() => ({ tenants: [] })),
    ])
      .then(([leadRes, tenRes]: any[]) => {
        setLeads(leadRes.leads || []);
        const t = (tenRes.tenants || []).find((x: any) => x.id === tenantId);
        setClientName(t?.brand_name || t?.name || tenantId.slice(0, 8));
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [tenantId]);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = useMemo(() => {
    let rows = [...leads];
    const needle = q.trim().toLowerCase();
    if (needle) {
      rows = rows.filter((l) =>
        [l.name, l.phone, l.city, l.website, l.source, l.tier]
          .filter(Boolean)
          .join(" ")
          .toLowerCase()
          .includes(needle)
      );
    }
    if (tierSort === "A_first") {
      rows.sort(
        (a, b) =>
          (TIER_ORDER[(a.tier || "Z").toUpperCase()] ?? 9) -
          (TIER_ORDER[(b.tier || "Z").toUpperCase()] ?? 9)
      );
    } else if (tierSort === "C_first") {
      rows.sort(
        (a, b) =>
          (TIER_ORDER[(b.tier || "A").toUpperCase()] ?? -1) -
          (TIER_ORDER[(a.tier || "A").toUpperCase()] ?? -1)
      );
    }
    return rows;
  }, [leads, q, tierSort]);

  const tierCounts = useMemo(() => {
    const c = { A: 0, B: 0, C: 0 };
    for (const l of leads) {
      const t = (l.tier || "").toUpperCase();
      if (t === "A" || t === "B" || t === "C") c[t]++;
    }
    return c;
  }, [leads]);

  async function removeLead(row: LeadRow) {
    if (!row.assignment_id) return;
    const label = row.name || row.assignment_id;
    if (
      !confirm(
        `Remove "${label}" from this client?\n\nOnly this assignment is removed. The global lead stays in the pool.`
      )
    )
      return;
    setBusyId(row.assignment_id);
    setMsg("");
    try {
      await admin.unassignLead(row.assignment_id, tenantId);
      setLeads((prev) => prev.filter((x) => x.assignment_id !== row.assignment_id));
      setMsg(`Removed ${label}`);
    } catch (e: any) {
      setMsg(e.message || "Remove failed");
    } finally {
      setBusyId(null);
    }
  }


  async function submitAdd() {
    if (!addForm.name.trim()) {
      setMsg("Name is required");
      return;
    }
    setAdding(true);
    setMsg("");
    try {
      await admin.createLead({ tenant_id: tenantId, ...addForm, source: "manual" });
      setMsg(`Added "${addForm.name}" — visible in client CRM`);
      setShowAdd(false);
      setAddForm({
        name: "", phone: "", email: "", website: "", address: "", city: "", state: "",
        category: "", tier: "B", owner_name: "", pitch_line: "", linkedin_url: "", instagram_handle: "",
      });
      load();
    } catch (e: any) {
      setMsg(e?.message || "Add failed");
    } finally {
      setAdding(false);
    }
  }

  function cycleTierSort() {
    setTierSort((s) => (s === "default" ? "A_first" : s === "A_first" ? "C_first" : "default"));
  }

  if (error) {
    return (
      <div className="p-8">
        <div className="rounded-xl border border-rust-500/30 bg-rust-500/10 px-4 py-3 text-sm text-rust-300">
          {error}
        </div>
        <button aria-label="Action" type="button" className="mt-4 text-sm text-brand-500 hover:underline" onClick={load}>
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6 p-6 md:p-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Link href="/admin/leads" className="text-xs text-ink-faint hover:text-brand-500">
            ← All clients
          </Link>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-ink">
            Leads · {clientName || "Client"}
          </h1>
          <p className="mt-1 text-sm text-ink-muted">
            {loading
              ? "Loading…"
              : `${leads.length} assigned · A ${tierCounts.A} · B ${tierCounts.B} · C ${tierCounts.C}`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            className="rounded-lg border border-line bg-surface-2 px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-brand-500/50 focus:outline-none"
            data-search="1" placeholder="Search name, city, phone…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <button aria-label="Action"
            type="button"
            onClick={cycleTierSort}
            className="rounded-lg border border-line bg-surface-2 px-3 py-2 text-sm text-ink hover:border-brand-500/40 hover:text-brand-700 dark:text-brand-300"
          >
            Tier sort:{" "}
            {tierSort === "default" ? "Default" : tierSort === "A_first" ? "A → C" : "C → A"}
          </button>
          <button aria-label="Action"
            type="button"
            onClick={load}
            className="rounded-lg border border-line bg-surface-2 px-3 py-2 text-sm text-ink-muted hover:text-ink"
          >
            Refresh
          </button>
          <button aria-label="Action"
            type="button"
            onClick={() => setShowAdd(true)}
            className="rounded-lg bg-brand-500 px-3 py-2 text-sm font-medium text-white hover:bg-brand-600"
          >
            + Add lead
          </button>
        </div>
      </header>

      {msg && (
        <p className="rounded-lg border border-line bg-surface-2 px-3 py-2 text-sm text-ink-muted">
          {msg}
        </p>
      )}

      {showAdd && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-line bg-surface p-6 shadow-xl">
            <h2 className="text-lg font-semibold text-ink">Add lead to client</h2>
            <p className="mt-1 text-xs text-ink-faint">Creates a lead and assigns it so it appears in the client CRM.</p>
            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              {(["name","phone","email","website","address","city","state","category","owner_name","linkedin_url","instagram_handle","pitch_line"] as const).map((k) => (
                <label key={k} className={`block text-xs ${k === "pitch_line" || k === "address" ? "sm:col-span-2" : ""}`}>
                  <span className="text-ink-faint uppercase">{k.replace(/_/g, " ")}{k === "name" ? " *" : ""}</span>
                  <input
                    className="mt-1 w-full rounded-lg border border-line bg-surface-2 px-3 py-2 text-sm text-ink"
                    value={(addForm as any)[k] || ""}
                    onChange={(e) => setAddForm((f) => ({ ...f, [k]: e.target.value }))}
                  />
                </label>
              ))}
              <label className="block text-xs">
                <span className="text-ink-faint uppercase">tier</span>
                <select
                  className="mt-1 w-full rounded-lg border border-line bg-surface-2 px-3 py-2 text-sm text-ink"
                  value={addForm.tier}
                  onChange={(e) => setAddForm((f) => ({ ...f, tier: e.target.value }))}
                >
                  <option value="A">A</option>
                  <option value="B">B</option>
                  <option value="C">C</option>
                </select>
              </label>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button aria-label="Action" type="button" onClick={() => setShowAdd(false)} className="rounded-lg border border-line px-3 py-2 text-sm text-ink-muted">Cancel</button>
              <button aria-label="Action" type="button" disabled={adding} onClick={submitAdd} className="rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
                {adding ? "Adding…" : "Add to client"}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-line bg-surface/40 shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[980px] text-left text-sm">
            <thead>
              <tr className="border-b border-line bg-surface-2/90 text-[11px] uppercase tracking-wide text-ink-faint">
                <th className="px-4 py-3 font-medium">Business</th>
                <th className="px-4 py-3 font-medium">Phone</th>
                <th className="px-4 py-3 font-medium">Location</th>
                <th className="px-4 py-3 font-medium">Website</th>
                <th className="px-4 py-3 font-medium">Source</th>
                <th className="px-4 py-3 font-medium">
                  <button aria-label="Action" type="button" className="hover:text-brand-500" onClick={cycleTierSort}>
                    Tier {tierSort === "A_first" ? "↑" : tierSort === "C_first" ? "↓" : ""}
                  </button>
                </th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Assigned</th>
                <th className="px-4 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line/80">
              {!loading && filtered.length === 0 && (
                <tr>
                  <td colSpan={9} className="px-4 py-12 text-center text-ink-faint">
                    No leads for this client{q ? " matching search" : ""}.
                  </td>
                </tr>
              )}
              {filtered.map((l) => (
                <tr key={l.assignment_id || l.lead_id} className="hover:bg-surface-2/50">
                  <td className="px-4 py-3">
                    <Link
                      href={`/admin/leads/${tenantId}/${l.lead_id}`}
                      className="font-medium text-ink hover:text-brand-400 hover:underline"
                    >
                      {l.name || "—"}
                    </Link>
                    {l.category && <div className="text-xs text-ink-faint">{l.category}</div>}
                    {l.tier && (
                      <div className="mt-0.5 text-[10px] text-ink-faint">Score {l.score ?? "—"}</div>
                    )}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-ink-muted">{l.phone || "—"}</td>
                  <td className="px-4 py-3 text-ink-muted">
                    {[l.city, l.state].filter(Boolean).join(", ") || "—"}
                  </td>
                  <td className="max-w-[180px] truncate px-4 py-3">
                    {l.website ? (
                      <a
                        href={l.website.startsWith("http") ? l.website : `https://${l.website}`}
                        target="_blank"
                        rel="noreferrer"
                        className="text-brand-400/90 hover:underline"
                      >
                        {l.website.replace(/^https?:\/\//, "")}
                      </a>
                    ) : (
                      <span className="text-ink-muted">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[11px] text-ink-muted">
                      {l.source || "—"}
                    </span>
                  </td>
                  <td className="px-4 py-3">{tierBadge(l.tier)}</td>
                  <td className="px-4 py-3">{statusBadge(l.display_status || (l.delivery_status === "failed" && l.delivery_channel === "portal" ? "Assigned" : (l.delivery_status || l.status)))}</td>
                  <td className="px-4 py-3 text-xs text-ink-faint">
                    {(l.delivered_at || l.assigned_at || "").replace("T", " ").slice(0, 16) || "—"}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button aria-label="Action"
                      type="button"
                      disabled={!l.assignment_id || busyId === l.assignment_id}
                      onClick={() => removeLead(l)}
                      className="rounded-md border border-rust-500/30 px-2.5 py-1 text-xs font-medium text-rust-300 hover:bg-rust-500/10 disabled:opacity-40"
                    >
                      {busyId === l.assignment_id ? "…" : "Remove"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <p className="text-xs text-ink-muted">
        Remove only unassigns from this client. Global lead data stays available for other tenants.
      </p>
    </div>
  );
}
