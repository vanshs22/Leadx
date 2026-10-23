"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { billing } from "@/lib/api";
import { ErrorState } from "@/components/ui/States";
import { PageHeader } from "@/components/ui/Primitives";
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
  return <div className="page max-w-4xl">
    <PageHeader eyebrow="Workspace settings" title="Billing" description="View the plan and capacity returned by the billing API. Checkout, invoices, and payment methods are not exposed here." meta={<FreshnessNote label="Billing status" detail="authoritative API snapshot" />} />
    {loading ? <div className="card-pad text-sm text-ink-faint" aria-busy="true">Loading billing…</div> : error ? <ErrorState error={error} onRetry={load} /> : data ? <>
      <section className="grid gap-4 sm:grid-cols-3">
        <div className="card-pad"><p className="stat-label">Current plan</p><p className="mt-2 text-2xl font-semibold capitalize">{data.plan}</p></div>
        <div className="card-pad"><p className="stat-label">Lead capacity</p><p className="mt-2 text-2xl font-semibold">{Number(data.leads_per_month_limit || 0).toLocaleString()}</p><p className="mt-1 text-xs text-ink-faint">qualified leads / month</p></div>
        <div className="card-pad"><p className="stat-label">Billing status</p><p className="mt-2 text-2xl font-semibold capitalize">{data.billing_status || "free"}</p></div>
      </section>
      <section className="card-pad flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between"><div><h2 className="text-base font-semibold">Change plan</h2><p className="mt-1 text-sm text-ink-muted">Upgrade capacity or move to Agency for multi-client operations.</p></div><Link href="/pricing" className="btn-primary">View plans</Link></section>
      <section className="card-pad"><h2 className="text-base font-semibold">Billing architecture</h2><p className="mt-2 text-sm leading-6 text-ink-muted">Checkout is handled by Stripe. LeadX stores subscription state and plan limits, while provider price IDs stay server-side in environment variables. Invoice history and payment-method management are unavailable in this workspace surface.</p></section>
    </> : null}
  </div>;
}
