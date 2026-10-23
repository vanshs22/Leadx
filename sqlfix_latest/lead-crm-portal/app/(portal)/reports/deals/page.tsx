"use client";

/**
 * Deal-level reporting: what the pipeline is actually worth, and how much
 * of the closed revenue traces back to leads we delivered. The attribution
 * number is the one that justifies the subscription at renewal.
 */
import { useEffect, useState } from "react";

import { crmExtras, type DealReport } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";

const WINDOWS = [
  { days: 90, label: "90 days" },
  { days: 180, label: "6 months" },
  { days: 365, label: "12 months" },
];

export default function DealReportsPage() {
  const [data, setData] = useState<DealReport | null>(null);
  const [days, setDays] = useState(365);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    crmExtras
      .dealReport(getTenantId(), days)
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [days]);

  const cur = data?.currency || "USD";
  const money = (v: number) =>
    new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: cur,
      maximumFractionDigits: 0,
    }).format(v || 0);

  const maxMonth = Math.max(1, ...(data?.monthly_revenue || []).map((m) => m.value));
  const maxStage = Math.max(1, ...(data?.by_stage || []).map((s) => s.value));

  return (
    <div className="page">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">Deal reporting</h1>
          <p className="page-sub">Revenue, forecast and lead attribution</p>
        </div>
        <div className="flex gap-2">
          {WINDOWS.map((w) => (
            <button aria-label="Action"
              key={w.days}
              onClick={() => setDays(w.days)}
              className={
                days === w.days
                  ? "pill bg-brand-600 text-white"
                  : "pill border border-line bg-surface-2 text-ink-muted"
              }
            >
              {w.label}
            </button>
          ))}
        </div>
      </header>

      {error && <p className="text-sm text-rust-500 dark:text-rust-300">{error}</p>}
      {loading && <p className="text-sm text-ink-faint">Loading…</p>}

      {data?.ok && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {[
              ["Revenue won", money(data.revenue_won), `${data.deals_won} deals`],
              [
                "Weighted forecast",
                money(data.forecast_weighted),
                `${data.open_deals} open · ${money(data.open_value)} gross`,
              ],
              ["Win rate", `${data.win_rate}%`, `avg ${money(data.avg_deal_value)}`],
              [
                "From delivered leads",
                money(data.revenue_from_delivered_leads),
                `${data.attribution_pct}% of won revenue`,
              ],
            ].map(([label, big, sub]) => (
              <div key={String(label)} className="card-pad">
                <div className="text-xs uppercase tracking-wide text-ink-faint">{label}</div>
                <div className="mt-1 text-2xl font-semibold text-ink">{big}</div>
                <div className="mt-0.5 text-xs text-ink-faint">{sub}</div>
              </div>
            ))}
          </div>

          {data.avg_cycle_days != null && (
            <p className="text-xs text-ink-faint">
              Average time from created to won: {data.avg_cycle_days} days.
            </p>
          )}

          <section className="card-pad space-y-3">
            <h2 className="text-sm font-semibold text-ink">Open pipeline by stage</h2>
            {!data.by_stage.length && <p className="text-sm text-ink-faint">No open deals.</p>}
            {data.by_stage.map((s) => (
              <div key={s.stage} className="space-y-1">
                <div className="flex justify-between text-xs">
                  <span className="text-ink">
                    {s.stage} <span className="text-ink-faint">({s.count})</span>
                  </span>
                  <span className="text-ink-muted">
                    {money(s.value)} · weighted {money(s.weighted)}
                  </span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-surface-3">
                  <div
                    className="h-full rounded-full bg-brand-500"
                    style={{ width: `${Math.round((s.value / maxStage) * 100)}%` }}
                  />
                </div>
              </div>
            ))}
          </section>

          <section className="card-pad space-y-3">
            <h2 className="text-sm font-semibold text-ink">Revenue by month</h2>
            {!data.monthly_revenue.length && (
              <p className="text-sm text-ink-faint">No closed-won deals in this window yet.</p>
            )}
            <div className="flex h-40 items-end gap-1">
              {data.monthly_revenue.map((m) => (
                <div key={m.month} className="flex flex-1 flex-col items-center gap-1">
                  <div
                    title={`${m.month}: ${money(m.value)}`}
                    className="w-full rounded-t bg-signal-500"
                    style={{ height: `${Math.max(2, Math.round((m.value / maxMonth) * 100))}%` }}
                  />
                  <span className="text-[10px] text-ink-faint">{m.month.slice(2)}</span>
                </div>
              ))}
            </div>
          </section>
        </>
      )}

      {data && !data.ok && (
        <p className="text-sm text-ink-faint">
          No deal data yet — convert a lead into a deal from the Leads page to start tracking.
        </p>
      )}
    </div>
  );
}
