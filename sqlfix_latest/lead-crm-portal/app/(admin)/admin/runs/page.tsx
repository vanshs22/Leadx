"use client";

import { useLiveLoader } from "@/lib/live";
import { useState, Fragment } from "react";
import Link from "next/link";
import { admin } from "@/lib/admin";
import { FunnelChart } from "@/components/charts/FunnelChart";
import { EmptyState, ErrorState, LoadingRows, PollingDeadlineState } from "@/components/ui/States";
import { FreshnessNote } from "@/components/ui/SurfaceMeta";

const STATUS_STYLE: Record<string, string> = {
  queued: "bg-surface-3 text-ink-muted",
  completed: "bg-signal-50 text-signal-700 dark:bg-signal-500/15 dark:text-signal-300",
  failed: "bg-rust-50 text-rust-700 dark:bg-rust-900/30 dark:text-rust-300",
  running: "bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-700 dark:text-brand-300",
};

export default function AdminRunsPage() {
  const [runs, setRuns] = useState<any[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const [pollingExpired, setPollingExpired] = useState(false);
  const [pollingReset, setPollingReset] = useState(0);

  const load = async () => {
    try {
      const d = await admin.runs();
      setRuns(d.runs || []);
      setError("");
      setLastUpdated(new Date().toLocaleTimeString());
    } catch (caught: any) {
      setError(caught?.message || "Could not load runs");
    } finally {
      setLoading(false);
    }
  };

  const refresh = () => {
    setPollingExpired(false);
    setPollingReset((value) => value + 1);
    void load();
  };

  useLiveLoader(load, 2_500, [pollingReset], {
    onDeadline: () => setPollingExpired(true),
  });

  if (loading && !runs.length) return <div className="page"><h1 className="page-title">Pipeline runs</h1><LoadingRows rows={5} cols={5} /></div>;
  if (error && !runs.length) return <div className="page"><ErrorState error={error} onRetry={refresh} /></div>;

  const total = runs.length;
  const completed = runs.filter((r) => r.status === "completed").length;
  const failed = runs.filter((r) => r.status === "failed").length;
  const totalDelivered = runs.reduce((s, r) => s + (r.delivered || 0), 0);

  return (
    <div className="page">
      <header>
        <h1 className="page-title">Pipeline runs</h1>
        <p className="page-sub">Recent discover → enrich → gate → assign → deliver jobs. Run telemetry refreshes while this tab is visible.</p>
        <div className="mt-2 flex flex-wrap items-center gap-3"><FreshnessNote label="Live run monitor" detail={lastUpdated ? `updated ${lastUpdated}` : "waiting for first response"} />{error ? <span className="text-[11px] text-rust-500 dark:text-rust-300">Refresh issue: {error}</span> : null}</div>
        {pollingExpired && <PollingDeadlineState onRefresh={refresh} />}
      </header>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="kpi">
          <div className="label">Total runs</div>
          <div className="kpi-value mt-2">{total}</div>
        </div>
        <div className="kpi">
          <div className="label">Completed</div>
          <div className="kpi-value mt-2 text-signal-600 dark:text-signal-300">{completed}</div>
        </div>
        <div className="kpi">
          <div className="label">Failed</div>
          <div className="kpi-value mt-2 text-rust-500 dark:text-rust-300">{failed}</div>
        </div>
        <div className="kpi">
          <div className="label">Leads delivered</div>
          <div className="kpi-value mt-2">{totalDelivered}</div>
        </div>
      </div>

      <div className="card overflow-hidden">
        <table className="w-full min-w-[900px] text-left text-sm">
          <thead className="border-b border-line bg-surface-3/60 text-xs uppercase tracking-wide text-ink-faint">
            <tr>
              <th className="px-4 py-3 font-medium">Started</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 font-medium">Funnel</th>
              <th className="px-4 py-3 font-medium">Delivered</th>
              <th className="px-4 py-3 font-medium">Error</th>
              <th className="px-4 py-3 font-medium" />
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {runs.map((r) => {
              const stages = [
                { stage: "Found", count: r.discovered ?? 0 },
                { stage: "Enriched", count: r.enriched ?? 0 },
                { stage: "Gated", count: r.gated_ok ?? 0 },
                { stage: "Assigned", count: r.assigned ?? 0 },
              ];
              const isOpen = expanded === r.id;
              return (
                <Fragment key={r.id}>
                  <tr
                    className="cursor-pointer hover:bg-surface-3/40"
                    onClick={() => setExpanded(isOpen ? null : r.id)}
                  >
                    <td className="px-4 py-3 text-xs text-ink-muted">
                      {r.started_at ? new Date(r.started_at).toLocaleString() : "—"}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`pill ${STATUS_STYLE[r.status] || "bg-surface-3 text-ink-muted"}`}>
                        {r.status}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1.5 font-mono text-xs tabular-nums text-ink-faint">
                        {stages.map((s, i) => (
                          <span key={s.stage} className="flex items-center gap-1.5">
                            {i > 0 && <span className="text-line">→</span>}
                            {s.count}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="px-4 py-3 font-mono tabular-nums text-ink">{r.delivered ?? "—"}</td>
                    <td className="max-w-[220px] truncate px-4 py-3 text-xs text-rust-500 dark:text-rust-300">
                      {r.error_message || ""}
                    </td>
                    <td className="px-4 py-3">
                      <Link
                        href={`/admin/runs/${r.id}`}
                        onClick={(e) => e.stopPropagation()}
                        className="text-xs font-medium text-brand-600 hover:underline dark:text-brand-700 dark:text-brand-300"
                      >
                        Board →
                      </Link>
                    </td>
                  </tr>
                  {isOpen && (
                    <tr key={`${r.id}-detail`} className="bg-surface-3/30">
                      <td colSpan={6} className="px-6 py-4">
                        <FunnelChart stages={stages} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
            {runs.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-10"><EmptyState title="No pipeline runs yet" hint="Start a pipeline run to see discovery, gating, assignment, and delivery evidence here." /></td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
