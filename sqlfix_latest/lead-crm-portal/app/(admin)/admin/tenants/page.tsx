"use client";

import { useLiveLoader } from "@/lib/live";
import { useState } from "react";
import Link from "next/link";
import { admin } from "@/lib/admin";
import { ErrorState, EmptyState, LoadingRows, PollingDeadlineState } from "@/components/ui/States";
import { FreshnessNote, UnavailableNote } from "@/components/ui/SurfaceMeta";

export default function AdminTenantsPage() {
  const [tenants, setTenants] = useState<any[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    name: "",
    plan: "starter",
    leads_per_month_limit: 100,
    allow_tier_b: false,
    delivery_channel: "telegram",
    bot_token: "",
    chat_id: "",
    // Client login — admin sets these directly; the client only logs in,
    // no signup flow.
    login_email: "",
    login_password: "",
    login_full_name: "",
    // Combined ICP fields
    vertical: "",
    geo: "",
    keywords: "",
    offer_category: "lead_gen_service",
    // Branding
    brand_color: "",
    logo_url: "",
  });
  const [msg, setMsg] = useState("");
  const [credentials, setCredentials] = useState<{ email: string; password: string } | null>(null);

  const [pollingExpired, setPollingExpired] = useState(false);
  const [pollingReset, setPollingReset] = useState(0);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const d = await admin.tenants();
      setTenants(d.tenants || []);
    } catch (caught) {
      setError(caught);
    } finally {
      setLoading(false);
    }
  }

  const refresh = () => {
    setPollingExpired(false);
    setPollingReset((value) => value + 1);
    void load();
  };

  useLiveLoader(load, 30_000, [pollingReset], {
    onDeadline: () => setPollingExpired(true),
  });

  async function create() {
    setMsg("");
    try {
      const delivery_config: Record<string, string> = {};
      if (form.bot_token) delivery_config.bot_token = form.bot_token;
      if (form.chat_id) delivery_config.chat_id = form.chat_id;
      const created: any = await admin.createTenant({
        name: form.name,
        plan: form.plan,
        leads_per_month_limit: Number(form.leads_per_month_limit),
        allow_tier_b: form.allow_tier_b,
        delivery_channel: form.delivery_channel,
        delivery_config,
        branding: {
          primary_color: form.brand_color || undefined,
          logo_url: form.logo_url || undefined,
        },
      });
      const tenantId = created?.id || created?.tenant_id;
      // Client login — admin-provisioned, no self-serve signup.
      if (tenantId && form.login_email.trim()) {
        try {
          const userResult: any = await admin.createTenantUser(tenantId, {
            email: form.login_email.trim(),
            password: form.login_password.trim() || undefined,
            full_name: form.login_full_name.trim() || undefined,
          });
          setCredentials({ email: userResult.email, password: userResult.password });
        } catch (e: any) {
          setMsg(`Client created, but login setup failed: ${e.message}`);
        }
      }
      // Optionally create first ICP in the same flow
      if (tenantId && form.vertical.trim()) {
        try {
          await admin.createIcp({
            tenant_id: tenantId,
            name: `${form.vertical} — ${form.geo || "default"}`,
            vertical: form.vertical.trim(),
            geo: form.geo.split(",").map((s) => s.trim()).filter(Boolean),
            keywords: form.keywords.split(",").map((s) => s.trim()).filter(Boolean),
            offer_category: form.offer_category || "lead_gen_service",
            active: true,
          });
        } catch {
          /* ICP creation is best-effort in combined flow */
        }
      }
      setShowForm(false);
      setMsg(
        tenantId && form.vertical.trim()
          ? "Client + ICP created"
          : "Client created"
      );
      refresh();
    } catch (e: any) {
      setMsg(e.message);
    }
  }

  if (loading) return <div className="page"><h1 className="page-title">Clients</h1><LoadingRows rows={5} cols={5} /></div>;
  if (error) return <div className="space-y-3 p-8"><h1 className="text-xl font-semibold text-ink">Clients</h1><ErrorState error={error} onRetry={load} /><Link href="/admin/login" className="text-sm text-brand-600 hover:underline">Re-login</Link></div>;

  return (
    <div className="page">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-ink">Clients</h1>
          <p className="page-sub">Tenant workspaces, delivery posture, quota, and client access. Secrets are only shown at creation time.</p>
          <div className="mt-2"><FreshnessNote label="Control-plane snapshot" detail={`${tenants.length} clients loaded`} /></div>
        </div>
        <button aria-label="Action"
          onClick={() => setShowForm(!showForm)}
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-ink"
        >
          {showForm ? "Cancel" : "New client"}
        </button>
      </header>

      {pollingExpired && <PollingDeadlineState onRefresh={refresh} />}

      {msg && <p className="text-sm text-brand-500 dark:text-brand-700 dark:text-brand-300">{msg}</p>}

      {credentials && (
        <div className="card-pad space-y-2 border-signal-300 bg-signal-50 dark:border-signal-800/50 dark:bg-signal-900/20">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-signal-800 dark:text-signal-300">
              Client login created — hand these to the client now, they won't be shown again
            </h3>
            <button aria-label="Action" className="btn-ghost text-xs" onClick={() => setCredentials(null)}>
              Dismiss
            </button>
          </div>
          <div className="flex items-center gap-3 font-mono text-sm">
            <span className="text-ink-muted">Email:</span>
            <span className="text-ink">{credentials.email}</span>
          </div>
          <div className="flex items-center gap-3 font-mono text-sm">
            <span className="text-ink-muted">Password:</span>
            <span className="text-ink">{credentials.password}</span>
            <button aria-label="Action"
              className="btn-ghost text-xs"
              onClick={() => navigator.clipboard.writeText(credentials.password)}
            >
              Copy
            </button>
          </div>
        </div>
      )}

      {showForm && (
        <div className="grid gap-3 rounded-xl border border-line bg-surface-2/60 p-5 sm:grid-cols-2">
          <input
            placeholder="Agency / company name"
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <select
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm"
            value={form.plan}
            onChange={(e) => setForm({ ...form, plan: e.target.value })}
          >
            <option value="free">Free</option>
            <option value="starter">Starter</option>
            <option value="growth">Growth</option>
            <option value="agency">Agency</option>
          </select>
          <input
            type="number"
            placeholder="Monthly lead limit"
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm"
            value={form.leads_per_month_limit}
            onChange={(e) =>
              setForm({ ...form, leads_per_month_limit: Number(e.target.value) })
            }
          />
          <select
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm"
            value={form.delivery_channel}
            onChange={(e) => setForm({ ...form, delivery_channel: e.target.value })}
          >
            <option value="telegram">Telegram</option>
            <option value="webhook">Webhook</option>
            <option value="email">Email</option>
            <option value="csv">CSV only</option>
          </select>
          <input
            placeholder="Telegram bot token (optional)"
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm"
            value={form.bot_token}
            onChange={(e) => setForm({ ...form, bot_token: e.target.value })}
          />
          <input
            placeholder="Telegram chat id"
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm"
            value={form.chat_id}
            onChange={(e) => setForm({ ...form, chat_id: e.target.value })}
          />
          <label className="flex items-center gap-2 text-sm text-ink-muted sm:col-span-2">
            <input
              type="checkbox"
              checked={form.allow_tier_b}
              onChange={(e) => setForm({ ...form, allow_tier_b: e.target.checked })}
            />
            Allow Tier B leads
          </label>

          <div className="sm:col-span-2 border-t border-line pt-3 text-xs font-semibold uppercase tracking-wide text-ink-faint">
            Client login (admin-provisioned — client just signs in, no signup)
          </div>
          <input
            placeholder="Client email"
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm"
            value={form.login_email}
            onChange={(e) => setForm({ ...form, login_email: e.target.value })}
          />
          <input
            placeholder="Full name (optional)"
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm"
            value={form.login_full_name}
            onChange={(e) => setForm({ ...form, login_full_name: e.target.value })}
          />
          <div className="flex gap-2 sm:col-span-2">
            <input
              placeholder="Password — leave blank to auto-generate"
              className="flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-sm"
              value={form.login_password}
              onChange={(e) => setForm({ ...form, login_password: e.target.value })}
            />
            <button aria-label="Action"
              type="button"
              className="btn-secondary shrink-0 text-xs"
              onClick={() => {
                const chars = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789!@#$%";
                const pw = Array.from({ length: 14 }, () => chars[Math.floor(Math.random() * chars.length)]).join("");
                setForm({ ...form, login_password: pw });
              }}
            >
              Generate
            </button>
          </div>

          <div className="sm:col-span-2 border-t border-line pt-3 text-xs font-semibold uppercase tracking-wide text-ink-faint">
            First ICP (optional)
          </div>
          <input
            placeholder="Vertical (e.g. medspa)"
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm"
            value={form.vertical}
            onChange={(e) => setForm({ ...form, vertical: e.target.value })}
          />
          <input
            placeholder="Geo (comma-separated)"
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm"
            value={form.geo}
            onChange={(e) => setForm({ ...form, geo: e.target.value })}
          />
          <input
            placeholder="Keywords"
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm"
            value={form.keywords}
            onChange={(e) => setForm({ ...form, keywords: e.target.value })}
          />
          <input
            placeholder="Offer category"
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm"
            value={form.offer_category}
            onChange={(e) => setForm({ ...form, offer_category: e.target.value })}
          />

          <div className="sm:col-span-2 border-t border-line pt-3 text-xs font-semibold uppercase tracking-wide text-ink-faint">
            Branding (optional)
          </div>
          <input
            placeholder="Brand color (#hex)"
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm"
            value={form.brand_color}
            onChange={(e) => setForm({ ...form, brand_color: e.target.value })}
          />
          <input
            placeholder="Logo URL"
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm"
            value={form.logo_url}
            onChange={(e) => setForm({ ...form, logo_url: e.target.value })}
          />

          <button aria-label="Action"
            onClick={create}
            disabled={!form.name}
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm text-ink disabled:opacity-50 sm:col-span-2"
          >
            Create client{form.vertical.trim() ? " + ICP" : ""}
          </button>
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-line">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-line bg-surface-2 text-xs uppercase text-ink-faint">
            <tr>
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">Plan</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Quota</th>
              <th className="px-4 py-3">Delivery</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {tenants.map((t) => (
              <tr key={t.id} className="hover:bg-surface-3/50">
                <td className="px-4 py-3 font-medium text-ink">{t.name}</td>
                <td className="px-4 py-3 text-ink-muted">{t.plan}</td>
                <td className="px-4 py-3">
                  <span
                    className={`rounded px-2 py-0.5 text-xs ${
                      t.status === "active"
                        ? "bg-signal-900/50 text-signal-300"
                        : "bg-surface-3 text-ink-muted"
                    }`}
                  >
                    {t.status}
                  </span>
                </td>
                <td className="px-4 py-3 tabular-nums text-ink-muted">
                  {t.leads_per_month_limit}/mo
                </td>
                <td className="px-4 py-3 text-ink-faint">{t.delivery_channel || <UnavailableNote label="Delivery" />}</td>
                <td className="px-4 py-3 text-right">
                  <Link href={`/admin/tenants/${t.id}`} className="text-brand-500 hover:underline">
                    Manage
                  </Link>
                </td>
              </tr>
            ))}
            {tenants.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-8"><EmptyState title="No clients yet" hint="Create the first tenant to provision an ICP, delivery route, and optional client login." /></td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
