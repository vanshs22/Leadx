"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { billing } from "@/lib/api";
import { EmptyState, ErrorState } from "@/components/ui/States";
import { Badge, PageHeader, Panel, StatusBadge } from "@/components/ui/Primitives";
import { Icon } from "@/components/ui/Icon";
import { FreshnessNote } from "@/components/ui/SurfaceMeta";

export default function BillingSettingsPage() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const load = () => {
    setLoading(true);
    setError(null);
    billing.status().then(setData).catch(setError).finally(() => setLoading(false));
  };
  useEffect(() => { load(); }, []);
  // Usage and invoices are rendered only when the billing API actually returns them.
  const limit = Number(data?.leads_per_month_limit || 0);
  const usedRaw = data?.leads_used_this_month ?? data?.usage?.leads ?? null;
  const used = typeof usedRaw === "number" ? usedRaw : null;
  const usedPct = used != null && limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : null;
  const invoices: any[] = Array.isArray(data?.invoices) ? data.invoices : [];
  const status = data?.billing_status || "free";
  return <div className="page max-w-5xl">
    <PageHeader eyebrow="Workspace settings" title="Billing" description="View the plan and capacity returned by the billing API. Checkout, invoices, and payment methods are not exposed here." actions={<button type="button" aria-label="Refresh billing status" className="btn-secondary" onClick={load} disabled={loading}><Icon name="refresh" size={14} aria-hidden="true" />Refresh</button>} meta={<FreshnessNote label="Billing status" detail="authoritative API snapshot" />} />
    {loading ? <div className="grid gap-4 lg:grid-cols-3" aria-busy="true"><span className="sr-only">Loading billing…</span><div className="card-pad lg:col-span-2"><div className="skeleton h-3 w-24" /><div className="skeleton mt-3 h-7 w-40" /><div className="skeleton mt-5 h-2 w-full" /></div><div className="card-pad"><div className="skeleton h-3 w-24" /><div className="skeleton mt-3 h-7 w-28" /></div></div> : error ? <ErrorState error={error} onRetry={load} /> : data ? <>
      <div className="grid items-start gap-4 lg:grid-cols-3">
        <Panel
          title="Current plan"
          description="Plan and monthly capacity for this workspace."
          className="lg:col-span-2"
          actions={<StatusBadge status={status === "free" ? "active" : status} label={status} />}
          footer={<><span>Upgrade capacity or move to Agency for multi-client operations.</span><Link href="/pricing" className="btn-primary shrink-0">View plans</Link></>}
        >
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <div className="stat-label">Plan</div>
              <div className="mt-1.5 flex items-center gap-2"><span className="font-display text-[1.625rem] font-semibold capitalize leading-none text-ink">{data.plan}</span>{data.billing_interval ? <Badge tone="neutral" className="capitalize">{data.billing_interval}</Badge> : null}</div>
            </div>
            <div className="text-right">
              <div className="stat-label">Lead capacity</div>
              <div className="mt-1.5 text-[1.25rem] font-semibold tabular-nums text-ink">{limit.toLocaleString()}</div>
              <div className="text-xs text-ink-faint">qualified leads / month</div>
            </div>
          </div>
          <div className="mt-6">
            <div className="flex items-center justify-between text-xs">
              <span className="font-medium text-ink">Qualified leads this month</span>
              <span className="tabular-nums text-ink-muted">{used != null ? `${used.toLocaleString()} / ${limit.toLocaleString()}` : `— / ${limit.toLocaleString()}`}</span>
            </div>
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-surface-3" role="meter" aria-label="Monthly lead usage" aria-valuemin={0} aria-valuemax={limit || 0} aria-valuenow={used ?? 0}>
              <div className={`h-full rounded-full ${usedPct != null && usedPct >= 90 ? "bg-amber-500" : "bg-brand-600 dark:bg-brand-300"}`} style={{ width: `${usedPct ?? 0}%` }} />
            </div>
            <p className="field-hint">{used != null ? <span className="tabular-nums">{usedPct}% of monthly capacity used.</span> : "Usage for the current period isn't returned by the billing API yet."}</p>
          </div>
        </Panel>
        <Panel title="Payment" description="Managed by Stripe.">
          <dl className="space-y-3 text-[0.8125rem]">
            <div className="flex items-center justify-between gap-3"><dt className="text-ink-muted">Billing status</dt><dd><StatusBadge status={status === "free" ? "active" : status} label={status} /></dd></div>
            <div className="flex items-center justify-between gap-3"><dt className="text-ink-muted">Payment method</dt><dd><StatusBadge status={data.has_payment_method ? "connected" : "pending"} label={data.has_payment_method ? "On file" : "None"} /></dd></div>
            <div className="flex items-center justify-between gap-3"><dt className="text-ink-muted">Checkout</dt><dd><StatusBadge status={data.stripe_configured ? "connected" : "pending"} label={data.stripe_configured ? "Available" : "Not configured"} /></dd></div>
          </dl>
        </Panel>
      </div>
      <Panel title="Invoices" description="Billing history for this workspace." bodyClassName={invoices.length ? "p-0" : "panel-body"}>
        {invoices.length ? (
          <div className="overflow-x-auto">
            <table className="data-table">
              <caption className="sr-only">Invoices</caption>
              <thead><tr><th scope="col">Invoice</th><th scope="col">Date</th><th scope="col">Status</th><th scope="col" className="!text-right">Amount</th></tr></thead>
              <tbody>{invoices.map((inv, i) => <tr key={inv.id || i}><td><code className="font-mono text-xs">{inv.number || inv.id || "—"}</code></td><td className="tabular-nums text-ink-muted">{String(inv.created_at || inv.date || "").slice(0, 10) || "—"}</td><td><StatusBadge status={inv.status} /></td><td className="text-right tabular-nums">{inv.amount != null ? new Intl.NumberFormat(undefined, { style: "currency", currency: String(inv.currency || "USD").toUpperCase() }).format(Number(inv.amount) || 0) : "—"}</td></tr>)}</tbody>
            </table>
          </div>
        ) : <EmptyState title="Invoice history isn't available here" hint="Checkout is handled by Stripe. LeadX stores subscription state and plan limits, while provider price IDs stay server-side in environment variables. Invoice history and payment-method management are unavailable in this workspace surface." />}
      </Panel>
    </> : null}
  </div>;
}
