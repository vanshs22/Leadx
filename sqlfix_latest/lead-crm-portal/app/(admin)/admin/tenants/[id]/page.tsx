"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { admin } from "@/lib/admin";
import { PageHeader, Notice } from "@/components/ui/Primitives";
import { ErrorState, LoadingRows } from "@/components/ui/States";

export default function AdminTenantDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [data, setData] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const [stripe, setStripe] = useState({ customer: "", item: "" });
  const [inviteEmail, setInviteEmail] = useState("");
  const [users, setUsers] = useState<any[]>([]);
  const [loginForm, setLoginForm] = useState({ email: "", password: "", full_name: "" });
  const [loginCreds, setLoginCreds] = useState<{ email: string; password: string } | null>(null);

  function load() {
    admin.tenant(id).then(setData).catch((e) => setMsg(e.message));
    admin.tenantUsers(id).then((d: any) => setUsers(d.users || [])).catch(() => {});
  }

  useEffect(() => {
    load();
  }, [id]);

  async function setStatus(status: string) {
    await admin.updateTenant(id, { status });
    setMsg(`Status → ${status}`);
    load();
  }

  async function saveQuota(limit: number) {
    await admin.updateTenant(id, { leads_per_month_limit: limit });
    setMsg("Quota updated");
    load();
  }

  async function linkStripe() {
    await admin.billingLink({
      tenant_id: id,
      stripe_customer_id: stripe.customer,
      stripe_subscription_item_id: stripe.item || undefined,
    });
    setMsg("Stripe linked");
    load();
  }

  async function reportBilling() {
    const r = await admin.billingReport(id);
    setMsg(JSON.stringify(r));
  }

  async function createLogin() {
    if (!loginForm.email.trim()) {
      setMsg("Email required for client login");
      return;
    }
    try {
      const r: any = await admin.createTenantUser(id, {
        email: loginForm.email.trim(),
        password: loginForm.password.trim() || undefined,
        full_name: loginForm.full_name.trim() || undefined,
        role: "owner",
      });
      if (!r?.ok && r?.error) {
        setMsg(String(r.error));
        return;
      }
      const pw = r.password || loginForm.password || "(see server response)";
      setLoginCreds({ email: r.email || loginForm.email.trim(), password: pw });
      setMsg(r.message || `Client login ready for ${r.email}`);
      if (r.member) {
        setUsers((prev) => {
          const rest = prev.filter((x) => x.email !== r.member.email);
          return [r.member, ...rest];
        });
      }
      setLoginForm({ email: "", password: "", full_name: "" });
      load();
    } catch (e: any) {
      setMsg(e?.message || e?.detail || "Failed to create login — check API logs / service role key");
    }
  }

  async function resetPw(authUserId: string) {
    try {
      const r: any = await admin.resetTenantUserPassword(id, { auth_user_id: authUserId });
      setLoginCreds({ email: users.find((u) => u.auth_user_id === authUserId)?.email || "", password: r.password });
      setMsg("Password reset — copy the new password below");
    } catch (e: any) {
      setMsg(e.message || "Reset failed");
    }
  }

  async function invite() {
    const r = await admin.teamInvite({
      tenant_id: id,
      email: inviteEmail,
      role: "member",
      send_email: true,
    });
    setMsg(r.ok ? `Invite: ${r.invite_url || "sent"}` : r.error);
  }

  if (!data && !msg) return <div className="page"><PageHeader eyebrow="Admin tenant" title="Loading tenant" /><LoadingRows rows={4} cols={3} /></div>;
  if (!data) return <div className="page"><ErrorState error={new Error(msg || "Tenant details are unavailable.")} onRetry={load} /></div>;

  const t = data.tenant;

  return (
    <div className="page space-y-6 p-8">
      <Link href="/admin/tenants" className="text-sm text-ink-faint hover:text-ink-muted">
        ← Clients
      </Link>
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-ink">{t.name}</h1>
          <p className="mt-1 font-mono text-xs text-ink-muted">{t.id}</p>
        </div>
        <div className="flex gap-2">
          {t.status === "active" ? (
            <button
              onClick={() => setStatus("paused")}
              className="rounded-lg border border-line px-3 py-1.5 text-sm text-ink-muted"
            >
              Pause
            </button>
          ) : (
            <button
              onClick={() => setStatus("active")}
              className="rounded-lg bg-signal-700 px-3 py-1.5 text-sm text-ink"
            >
              Activate
            </button>
          )}
          <Link
            href={`/admin/pipeline?tenant=${id}`}
            className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm text-ink"
          >
            Run pipeline
          </Link>
        </div>
      </header>

      {msg && <Notice tone={msg.toLowerCase().includes("failed") || msg.toLowerCase().includes("error") ? "danger" : "neutral"}>{msg}</Notice>}

      <div className="grid gap-4 lg:grid-cols-3">
        <section className="rounded-xl border border-line bg-surface-2/60 p-5">
          <h2 className="text-sm font-semibold text-ink">Plan & quota</h2>
          <dl className="mt-3 space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-ink-faint">Plan</dt>
              <dd className="text-ink">{t.plan}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-ink-faint">Status</dt>
              <dd className="text-ink">{t.status}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-ink-faint">Monthly limit</dt>
              <dd className="text-ink">{t.leads_per_month_limit}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-ink-faint">Delivery</dt>
              <dd className="text-ink">{t.delivery_channel}</dd>
            </div>
          </dl>
          <div className="mt-4 flex gap-2">
            {[50, 100, 300, 500].map((n) => (
              <button
                key={n}
                onClick={() => saveQuota(n)}
                className="rounded border border-line px-2 py-1 text-xs text-ink-muted hover:bg-surface-3"
              >
                {n}
              </button>
            ))}
          </div>
        </section>

        <section className="rounded-xl border border-line bg-surface-2/60 p-5">
          <h2 className="text-sm font-semibold text-ink">Stripe</h2>
          <p className="mt-1 text-xs text-ink-faint">
            Customer: {t.stripe_customer_id || "—"}
          </p>
          <input
            placeholder="cus_..."
            className="mt-3 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm"
            value={stripe.customer}
            onChange={(e) => setStripe({ ...stripe, customer: e.target.value })}
          />
          <input
            placeholder="si_... (optional)"
            className="mt-2 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm"
            value={stripe.item}
            onChange={(e) => setStripe({ ...stripe, item: e.target.value })}
          />
          <div className="mt-3 flex gap-2">
            <button onClick={linkStripe} className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm text-ink">
              Link
            </button>
            <button onClick={reportBilling} className="rounded-lg border border-line px-3 py-1.5 text-sm text-ink-muted">
              Report usage
            </button>
          </div>
        </section>

        <section className="rounded-xl border border-line bg-surface-2/60 p-5">
          <h2 className="text-sm font-semibold text-ink">Invite teammate</h2>
          <input
            type="email"
            placeholder="email@agency.com"
            className="mt-3 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm"
            value={inviteEmail}
            onChange={(e) => setInviteEmail(e.target.value)}
          />
          <button
            onClick={invite}
            className="mt-3 rounded-lg border border-line px-3 py-1.5 text-sm text-ink-muted"
          >
            Send invite
          </button>
        </section>
      </div>

      <section className="rounded-xl border border-line bg-surface-2/60 p-5">
        
      <div className="mt-6 rounded-2xl border border-line bg-surface-2/50 p-5">
        <h2 className="text-sm font-semibold text-ink">Client portal login</h2>
        <p className="mt-1 text-xs text-ink-faint">
          Set email + password for this client. They sign in at{" "}
          <code className="text-brand-400">/login</code> and only see this tenant&apos;s leads.
        </p>
        <div className="mt-3 grid gap-2 sm:grid-cols-3">
          <input
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm"
            placeholder="email@agency.com"
            value={loginForm.email}
            onChange={(e) => setLoginForm({ ...loginForm, email: e.target.value })}
          />
          <input
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm"
            placeholder="Full name (optional)"
            value={loginForm.full_name}
            onChange={(e) => setLoginForm({ ...loginForm, full_name: e.target.value })}
          />
          <input
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm"
            placeholder="Password (blank = auto)"
            value={loginForm.password}
            onChange={(e) => setLoginForm({ ...loginForm, password: e.target.value })}
          />
        </div>
        <button
          type="button"
          onClick={createLogin}
          className="mt-3 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white"
        >
          Create / attach login
        </button>
        {loginCreds && (
          <div className="mt-3 rounded-lg border border-brand-500/40 bg-brand-500/10 p-3 text-sm">
            <p className="font-medium text-brand-200">Hand these to the client once</p>
            <p className="mt-1 font-mono text-xs text-ink">Email: {loginCreds.email}</p>
            <p className="font-mono text-xs text-ink">Password: {loginCreds.password}</p>
            <p className="mt-1 text-xs text-ink-faint">Portal: /login</p>
          </div>
        )}
        {users.length > 0 && (
          <ul className="mt-4 space-y-2 text-sm">
            {users.map((u) => (
              <li
                key={u.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line px-3 py-2"
              >
                <span>
                  <span className="text-ink">{u.email}</span>
                  <span className="ml-2 text-xs text-ink-faint">
                    {u.role} · {u.status}
                  </span>
                </span>
                {u.auth_user_id && (
                  <button
                    type="button"
                    onClick={() => resetPw(u.auth_user_id)}
                    className="text-xs text-brand-500 hover:underline"
                  >
                    Reset password
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
<div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-ink">ICPs</h2>
          <Link href={`/admin/icps?tenant=${id}`} className="text-xs text-brand-500">
            Manage ICPs
          </Link>
        </div>
        <ul className="mt-3 divide-y divide-line">
          {(data.icps || []).map((i: any) => (
            <li key={i.id} className="flex justify-between py-2 text-sm">
              <span className="text-ink">
                {i.vertical} · {(i.geo || []).join(", ")}
              </span>
              <span className="text-ink-faint">{i.active ? "active" : "off"}</span>
            </li>
          ))}
          {(data.icps || []).length === 0 && (
            <li className="py-2 text-sm text-ink-faint">No ICPs — create under ICPs</li>
          )}
        </ul>
      </section>

      <section className="rounded-xl border border-line bg-surface-2/60 p-5">
        <h2 className="text-sm font-semibold text-ink">Recent usage</h2>
        <ul className="mt-3 space-y-1 text-sm text-ink-muted">
          {(data.usage || []).map((u: any) => (
            <li key={u.period} className="flex justify-between">
              <span>{u.period}</span>
              <span>
                delivered {u.leads_delivered || 0} · credited {u.leads_credited || 0}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
