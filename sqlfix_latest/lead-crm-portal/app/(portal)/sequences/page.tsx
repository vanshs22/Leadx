"use client";

import { useCallback, useEffect, useState } from "react";
import { crm } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { EmptyState, ErrorState, LoadingRows, PollingDeadlineState } from "@/components/ui/States";
import { FreshnessNote, UnavailableNote } from "@/components/ui/SurfaceMeta";
import { useLiveLoader } from "@/lib/live";
import { PageHeader } from "@/components/ui/Primitives";

type Step = { subject: string; body_html: string; delay_days: number };

export default function SequencesPage() {
  const [sequences, setSequences] = useState<any[]>([]);
  const [name, setName] = useState("");
  const [industry, setIndustry] = useState("");
  const [goal, setGoal] = useState("");
  const [steps, setSteps] = useState<Step[]>([{ subject: "", body_html: "", delay_days: 0 }]);
  const [msg, setMsg] = useState("");
  const [loadError, setLoadError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [aiBusy, setAiBusy] = useState<string | null>(null);
  const [pollingExpired, setPollingExpired] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const load = useCallback(() => {
    setLoading(true);
    setLoadError(null);
    return crm.sequences(getTenantId())
      .then((d: any) => setSequences(d.sequences || []))
      .catch((e) => { setLoadError(e); setMsg(""); })
      .finally(() => setLoading(false));
  }, []);
  useLiveLoader(load, 30_000, [refreshKey], { onDeadline: () => setPollingExpired(true) });
  useCrmChange(load, { tables: ["email_sequences", "email_sequence_steps", "email_sequence_enrollments", "email_sequence_sends"] });

  function updateStep(i: number, patch: Partial<Step>) {
    setSteps((s) => s.map((st, idx) => (idx === i ? { ...st, ...patch } : st)));
  }

  async function aiWrite(i: number) {
    setAiBusy(`write-${i}`);
    setMsg("");
    try {
      const r: any = await crm.aiWriteSequence(getTenantId(), {
        step_index: i + 1,
        delay_days: steps[i]?.delay_days ?? 0,
        industry: industry.trim(),
        goal: goal.trim(),
        subject_hint: steps[i]?.subject || "",
      });
      if (!r?.ok) {
        setMsg(r?.error || "AI write failed — set OPENROUTER_API_KEY or OPENAI_API_KEY in .env");
        return;
      }
      updateStep(i, {
        subject: r.subject || steps[i].subject,
        body_html: r.body_html || steps[i].body_html,
      });
      setMsg(`AI wrote step ${i + 1} via ${r.provider || "llm"}`);
    } catch (e: any) {
      setMsg(e.message || "AI write failed");
    } finally {
      setAiBusy(null);
    }
  }

  async function aiRephrase(i: number) {
    if (!steps[i]?.subject && !steps[i]?.body_html) {
      setMsg("Add subject/body first, or use AI write");
      return;
    }
    setAiBusy(`rephrase-${i}`);
    setMsg("");
    try {
      const r: any = await crm.aiRephraseSequence(getTenantId(), {
        subject: steps[i].subject,
        body_html: steps[i].body_html,
        instruction: "Make it clearer, warmer, and more concise. Keep all {{merge}} tags.",
      });
      if (!r?.ok) {
        setMsg(r?.error || "AI rephrase failed — set an AI key in .env");
        return;
      }
      updateStep(i, {
        subject: r.subject || steps[i].subject,
        body_html: r.body_html || steps[i].body_html,
      });
      setMsg(`AI rephrased step ${i + 1} via ${r.provider || "llm"}`);
    } catch (e: any) {
      setMsg(e.message || "AI rephrase failed");
    } finally {
      setAiBusy(null);
    }
  }

  async function save() {
    if (!name.trim() || steps.some((s) => !s.subject.trim() || !s.body_html.trim())) {
      setMsg("Name and every step's subject/body are required");
      return;
    }
    setBusy(true);
    setMsg("");
    try {
      await crm.createSequence(getTenantId(), { name: name.trim(), steps });
      setName("");
      setSteps([{ subject: "", body_html: "", delay_days: 0 }]);
      setMsg("Sequence created");
      load();
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <PageHeader eyebrow="Engage" title="Follow-up sequences" description="Build follow-up systems once, inspect delivery outcomes, and keep provider-generated copy labeled as such." meta={<FreshnessNote label="Live sequence telemetry" detail={`${sequences.length} sequences · read-side refresh only`} />} />
      {pollingExpired && <PollingDeadlineState onRefresh={() => { setPollingExpired(false); setRefreshKey((value) => value + 1); }} />}

      <div className="card-pad space-y-4">
        <h2 className="text-sm font-semibold text-ink">New sequence</h2>
        <input className="input" placeholder="Sequence name (e.g. Initial outreach)" value={name} onChange={(e) => setName(e.target.value)} />
        <div className="grid gap-2 sm:grid-cols-2">
          <input className="input" placeholder="Industry context for AI (e.g. NJ med spas)" value={industry} onChange={(e) => setIndustry(e.target.value)} />
          <input className="input" placeholder="AI goal (e.g. book a 15-min call)" value={goal} onChange={(e) => setGoal(e.target.value)} />
        </div>

        {steps.map((s, i) => (
          <div key={i} className="space-y-2 rounded-xl2 border border-line p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="label">Step {i + 1}</span>
              <div className="flex flex-wrap items-center gap-2 text-xs text-ink-muted">
                <span className="flex items-center gap-1">
                  Send after
                  <input
                    type="number"
                    min={0}
                    className="input w-16 py-1"
                    value={s.delay_days}
                    onChange={(e) => updateStep(i, { delay_days: parseInt(e.target.value) || 0 })}
                  />
                  day(s)
                </span>
                <button aria-label={`AI write step ${i + 1}`}
                  className="rounded-lg border border-brand-500/40 bg-brand-500/10 px-2.5 py-1 font-medium text-brand-200 hover:bg-brand-500/20 disabled:opacity-50"
                >
                  {aiBusy === `write-${i}` ? "Writing…" : "AI write"}
                </button>
                <button aria-label={`AI rephrase step ${i + 1}`}
                  className="rounded-lg border border-line px-2.5 py-1 font-medium text-ink hover:bg-surface-2 disabled:opacity-50"
                >
                  {aiBusy === `rephrase-${i}` ? "Rephrasing…" : "AI rephrase"}
                </button>
              </div>
            </div>
            <input
              className="input"
              placeholder="Subject"
              value={s.subject}
              onChange={(e) => updateStep(i, { subject: e.target.value })}
            />
            <textarea
              className="input min-h-[120px] font-mono text-xs"
              placeholder="Body HTML — merge tags: {{name}} {{company}} {{owner}} {{city}} {{pitch}} {{tier}}"
              value={s.body_html}
              onChange={(e) => updateStep(i, { body_html: e.target.value })}
            />
          </div>
        ))}

        <div className="flex flex-wrap gap-2">
          <button aria-label="Add sequence step"
            onClick={() => setSteps((s) => [...s, { subject: "", body_html: "", delay_days: (s[s.length - 1]?.delay_days || 0) + 3 }])}
          >
            + Add step
          </button>
          <button aria-label="Create follow-up sequence" type="button" disabled={busy} onClick={save} className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
            {busy ? "Saving…" : "Create sequence"}
          </button>
        </div>
      {msg && <p className="rounded-xl border border-line bg-surface-2 px-4 py-2.5 text-sm text-ink-muted">{msg}</p>}
      </div>

      {loading ? <div className="card"><LoadingRows rows={4} cols={5} /></div> : loadError ? <ErrorState error={loadError} onRetry={load} /> : (
      <div className="mt-8 overflow-x-auto rounded-xl border border-line">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-line text-xs text-ink-faint">
            <tr>
              <th className="px-4 py-3">NAME</th>
              <th className="px-4 py-3">STEPS</th>
              <th className="px-4 py-3">ACTIVE</th>
              <th className="px-4 py-3">COMPLETED</th>
              <th className="px-4 py-3">STOPPED (REPLY)</th>
              <th className="px-4 py-3">STOPPED (OPT-OUT)</th>
            </tr>
          </thead>
          <tbody>
            {sequences.map((s) => (
              <tr key={s.id} className="border-b border-line/60">
                <td className="px-4 py-3 text-ink">{s.name}</td>
                <td className="px-4 py-3">{s.step_count ?? s.steps?.length ?? "—"}</td>
                <td className="px-4 py-3">{s.enrollment_counts?.active ?? <UnavailableNote label="Active" />}</td>
                <td className="px-4 py-3">{s.enrollment_counts?.completed ?? <UnavailableNote label="Completed" />}</td>
                <td className="px-4 py-3">{s.enrollment_counts?.stopped_reply ?? <UnavailableNote label="Reply stop" />}</td>
                <td className="px-4 py-3">{s.enrollment_counts?.stopped_optout ?? <UnavailableNote label="Opt-out stop" />}</td>
              </tr>
            ))}
            {sequences.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-10"><EmptyState title="No sequences yet" hint="Create a reusable follow-up path above, then enroll selected leads from Leads." /></td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      )}
    </div>
  );
}
