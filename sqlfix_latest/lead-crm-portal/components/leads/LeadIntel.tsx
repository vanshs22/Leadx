"use client";

import { useState } from "react";
import { crmTools } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { CopyButton } from "@/components/ui/CopyButton";

type Quality = Awaited<ReturnType<typeof crmTools.dataQuality>>;
type Drafts = Awaited<ReturnType<typeof crmTools.outreachDraft>>;
type Check = Awaited<ReturnType<typeof crmTools.refreshCheck>>;
const msg = (e: unknown) => (e instanceof Error ? e.message : "Something went wrong. Please try again.");

/** On-demand intelligence for one lead: data quality with a free-first recovery plan, evidence-led drafts, and a website change check. */
export function LeadIntel({ assignmentId }: { assignmentId: string }) {
  const tid = getTenantId();
  const [quality, setQuality] = useState<Quality | null>(null);
  const [drafts, setDrafts] = useState<Drafts | null>(null);
  const [check, setCheck] = useState<Check | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  async function run<T>(key: string, fn: () => Promise<T>, set: (v: T) => void) {
    if (!tid) return;
    setBusy(key); setError("");
    try { set(await fn()); } catch (e) { setError(msg(e)); } finally { setBusy(null); }
  }

  return (
    <section aria-label="Lead intelligence" className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn-secondary" disabled={busy !== null} onClick={() => run("q", () => crmTools.dataQuality(tid!, assignmentId), setQuality)}>{busy === "q" ? "Checking…" : "Data quality"}</button>
        <button type="button" className="btn-secondary" disabled={busy !== null} onClick={() => run("d", () => crmTools.outreachDraft(tid!, assignmentId), setDrafts)}>{busy === "d" ? "Drafting…" : "Draft outreach"}</button>
        <button type="button" className="btn-secondary" disabled={busy !== null} onClick={() => run("c", () => crmTools.refreshCheck(tid!, assignmentId), setCheck)}>{busy === "c" ? "Checking site…" : "Check website for changes"}</button>
      </div>
      {error && <p role="alert" className="text-[0.8125rem] text-rust-600 dark:text-rust-300">{error}</p>}

      {quality && (
        <div className="rounded-lg border border-line p-3 text-[0.8125rem]" aria-live="polite">
          <div className="flex items-baseline justify-between"><span className="label">Data quality</span><span className="tabular-nums">Grade {quality.grade} · confidence {quality.confidence}/100 · completeness {quality.completeness}%</span></div>
          {quality.recommendations.length === 0 ? <p className="mt-2 text-ink-muted">Nothing missing.</p> : (
            <ul className="mt-2 space-y-1.5">
              {quality.recommendations.slice(0, 5).map((r) => (
                <li key={r.field}><span className="font-medium">Missing {r.field.replace("_", " ")}</span>
                  <span className="text-ink-muted"> — try {r.recovery_order.length ? r.recovery_order.map((p) => `${p.provider}${p.paid ? " (paid)" : " (free)"}`).join(" → ") : "manual research"}</span></li>
              ))}
            </ul>
          )}
        </div>
      )}

      {check && (
        <div className="rounded-lg border border-line p-3 text-[0.8125rem]" aria-live="polite">
          <div className="label">Website check ({check.lane}{check.free ? ", free" : ""})</div>
          {check.baseline ? <p className="mt-1 text-ink-muted">Baseline saved. Run this again later to see what changed.</p>
            : check.changed ? <ul className="mt-1 list-disc pl-5">{check.changes.map((c, i) => <li key={i}>{c.type.replace(/_/g, " ")}</li>)}</ul>
            : <p className="mt-1 text-ink-muted">No meaningful change since the last check.</p>}
        </div>
      )}

      {drafts && (
        <div className="space-y-3" aria-live="polite">
          <p className="text-xs text-ink-faint">{drafts.note}</p>
          {drafts.drafts.map((d) => (
            <div key={d.angle} className="rounded-lg border border-line p-3 text-[0.8125rem]">
              <div className="flex items-center justify-between"><span className="label">{d.angle} angle · {d.words} words</span><CopyButton value={`Subject: ${d.subject}\n\n${d.body_text}`} label="Copy draft" /></div>
              <div className="mt-1 font-medium">{d.subject}</div>
              <pre className="mt-1 whitespace-pre-wrap font-sans text-ink-muted">{d.body_text}</pre>
              {d.evidence.length > 0 && <p className="mt-2 text-xs text-ink-faint">Based on: {d.evidence.join("; ")}</p>}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
