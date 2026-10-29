"use client";

import { useCallback, useEffect, useState } from "react";
import { crm } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { EmptyState, ErrorState, LoadingRows, PollingDeadlineState } from "@/components/ui/States";
import { FreshnessNote, UnavailableNote } from "@/components/ui/SurfaceMeta";
import { useLiveLoader } from "@/lib/live";
import { PageHeader, Panel, StatCard, StatGrid, StatusBadge } from "@/components/ui/Primitives";
import { Icon } from "@/components/ui/Icon";

type Step = { subject: string; body_html: string; delay_days: number };

/** Inline "unavailable" marker for table cells (UnavailableNote is a block). */
function Missing({ label }: { label: string }) {
  return <span className="text-xs text-ink-faint" title={`${label}: unavailable`}>—<span className="sr-only">{label} unavailable</span></span>;
}

export default function SequencesPage() {
  const [sequences, setSequences] = useState<any[]>([]);
  const [name, setName] = useState("");
  const [industry, setIndustry] = useState("");
  const [goal, setGoal] = useState("");
  const [prompt, setPrompt] = useState("");
  const [steps, setSteps] = useState<Step[]>([{ subject: "", body_html: "", delay_days: 0 }]);
  const [msg, setMsg] = useState("");
  const [mutationError, setMutationError] = useState<unknown>(null);
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
    setMutationError(null);
    setMsg("");
    try {
      const r: any = await crm.aiWriteSequence(getTenantId(), {
        step_index: i + 1,
        delay_days: steps[i]?.delay_days ?? 0,
        industry: industry.trim(),
        goal: goal.trim(),
        subject_hint: steps[i]?.subject || "",
        prompt: prompt.trim(),
      });
      if (!r?.ok) {
        setMutationError(new Error(r?.error || "AI write failed"));
        return;
      }
      updateStep(i, {
        subject: r.subject || steps[i].subject,
        body_html: r.body_html || steps[i].body_html,
      });
      setMsg(`AI wrote step ${i + 1} via ${r.provider || "llm"}`);
    } catch (e: any) {
      setMutationError(e);
      setMsg("");
    } finally {
      setAiBusy(null);
    }
  }

  async function transition(sequence: any, action: "activate" | "pause" | "resume" | "complete") {
    setMutationError(null);
    setMsg("");
    try {
      const result: any = await crm.transitionSequence(getTenantId(), sequence.id, action);
      if (!result?.ok) throw new Error(result?.error || "Sequence transition failed");
      setMsg(`Sequence ${action} saved`);
      await load();
    } catch (e) {
      setMutationError(e);
    }
  }

  async function removeSequence(sequence: any) {
    if (!window.confirm(`Delete ${sequence.name || "this sequence"}?`)) return;
    setMutationError(null);
    setMsg("");
    try {
      await crm.deleteSequence(getTenantId(), sequence.id);
      setMsg("Sequence deleted");
      await load();
    } catch (e) {
      setMutationError(e);
    }
  }

  async function aiRephrase(i: number) {
    if (!steps[i]?.subject && !steps[i]?.body_html) {
      setMsg("Add subject/body first, or use AI write");
      return;
    }
    setAiBusy(`rephrase-${i}`);
    setMutationError(null);
    setMsg("");
    try {
      const r: any = await crm.aiRephraseSequence(getTenantId(), {
        subject: steps[i].subject,
        body_html: steps[i].body_html,
        instruction: "Make it clearer, warmer, and more concise. Keep all {{merge}} tags.",
      });
      if (!r?.ok) {
        setMutationError(new Error(r?.error || "AI rephrase failed"));
        return;
      }
      updateStep(i, {
        subject: r.subject || steps[i].subject,
        body_html: r.body_html || steps[i].body_html,
      });
      setMsg(`AI rephrased step ${i + 1} via ${r.provider || "llm"}`);
    } catch (e: any) {
      setMutationError(e);
      setMsg("");
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
      setMutationError(e);
      setMsg("");
    } finally {
      setBusy(false);
    }
  }

  const sumCount = (key: string): number | null => {
    const values = sequences.map((s) => s.enrollment_counts?.[key]).filter((v) => typeof v === "number") as number[];
    return values.length ? values.reduce((a, b) => a + b, 0) : null;
  };
  const activeSequences = sequences.filter((s) => (s.status || "").toLowerCase() === "active").length;
  const cumulativeDay = (i: number) => steps.slice(0, i + 1).reduce((acc, st) => acc + (st.delay_days || 0), 0);
  const msgTone = /fail|required|error/i.test(msg) ? "notice-danger" : /created|wrote|rephrased/i.test(msg) ? "notice-success" : "notice-neutral";

  return (
    <div className="page">
      <PageHeader eyebrow="Engage" title="Follow-up sequences" description="Build follow-up systems once, inspect delivery outcomes, and keep provider-generated copy labeled as such." meta={<FreshnessNote label="Live sequence telemetry" detail={`${sequences.length} sequences · read-side refresh only`} />} />
      {pollingExpired && <PollingDeadlineState onRefresh={() => { setPollingExpired(false); setRefreshKey((value) => value + 1); }} />}

      <StatGrid cols={4}>
        <StatCard label="Sequences" value={<span className="tabular-nums">{sequences.length}</span>} hint={<span className="tabular-nums">{activeSequences} active</span>} tone="brand" icon={<Icon name="sequence" size={16} />} />
        <StatCard label="Active enrollments" value={sumCount("active") ?? "—"} hint="Currently receiving steps" tone="success" />
        <StatCard label="Stopped on reply" value={sumCount("stopped_reply") ?? "—"} hint="Prospect responded" tone="neutral" />
        <StatCard label="Stopped on opt-out" value={sumCount("stopped_optout") ?? "—"} hint="Unsubscribed or undeliverable" tone="warn" />
      </StatGrid>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel
          className="lg:col-span-2"
          title="New sequence"
          description="Each step sends after the delay you set, counted from the previous step."
          actions={<span className="badge-neutral tabular-nums">{steps.length} {steps.length === 1 ? "step" : "steps"}</span>}
          footer={
            <>
              <span>Merge tags: <code className="font-mono">{"{{name}} {{company}} {{owner}} {{city}} {{pitch}} {{tier}}"}</code></span>
              <span className="tabular-nums">Total span {cumulativeDay(steps.length - 1)} days</span>
            </>
          }
        >
          <div className="space-y-5">
            <div>
              <label className="field-label" htmlFor="seq-name">Sequence name</label>
              <input id="seq-name" className="input" placeholder="Sequence name (e.g. Initial outreach)" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="field-label" htmlFor="seq-industry">Industry context</label>
                <input id="seq-industry" className="input" placeholder="Industry context for AI (e.g. NJ med spas)" value={industry} onChange={(e) => setIndustry(e.target.value)} />
                <p className="field-hint">Used only when you ask AI to write a step.</p>
              </div>
              <div>
                <label className="field-label" htmlFor="seq-goal">Goal</label>
                <input id="seq-goal" className="input" placeholder="AI goal (e.g. book a 15-min call)" value={goal} onChange={(e) => setGoal(e.target.value)} />
                <p className="field-hint">The outcome each step should push toward.</p>
              </div>
            </div>
            <div>
              <label className="field-label" htmlFor="seq-prompt">Email instructions</label>
              <textarea id="seq-prompt" className="textarea min-h-[80px]" placeholder="Describe the email you want, tone, offer, CTA, and anything to avoid" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
              <p className="field-hint">Passed to the configured AI provider for each AI-written step.</p>
            </div>

            <div>
              <div className="label mb-3">Step timeline</div>
              <ol className="relative">
                {steps.map((s, i) => (
                  <li key={i} className="relative pb-5 pl-10 last:pb-0">
                    {i < steps.length - 1 ? <span className="absolute left-[13px] top-7 h-[calc(100%-1.5rem)] w-px bg-line-strong" aria-hidden="true" /> : null}
                    <span className="absolute left-0 top-0 flex h-7 w-7 items-center justify-center rounded-full border border-brand-200 bg-brand-50 text-xs font-semibold tabular-nums text-brand-700 dark:border-brand-500/40 dark:bg-brand-500/15 dark:text-brand-200" aria-hidden="true">
                      {i + 1}
                    </span>
                    <div className="space-y-3 rounded-lg border border-line bg-surface-2 p-3.5">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-[0.8125rem] font-semibold text-ink">Step {i + 1}</span>
                          <span className="badge-neutral tabular-nums">Day {cumulativeDay(i)}</span>
                        </div>
                        <div className="flex flex-wrap items-center gap-2 text-xs text-ink-muted">
                          <label className="flex items-center gap-1.5" htmlFor={`seq-delay-${i}`}>
                            Send after
                            <input
                              id={`seq-delay-${i}`}
                              type="number"
                              min={0}
                              className="input h-8 w-16 min-h-0 py-1 tabular-nums"
                              value={s.delay_days}
                              onChange={(e) => updateStep(i, { delay_days: parseInt(e.target.value) || 0 })}
                            />
                            day(s)
                          </label>
                          <button aria-label={`AI write step ${i + 1}`} type="button" className="btn-secondary btn-sm" disabled={aiBusy !== null} onClick={() => void aiWrite(i)}>
                            <Icon name="spark" size={12} />
                            {aiBusy === `write-${i}` ? "Writing…" : "AI write"}
                          </button>
                          <button aria-label={`AI rephrase step ${i + 1}`} type="button" className="btn-ghost btn-sm" disabled={aiBusy !== null} onClick={() => void aiRephrase(i)}>
                            {aiBusy === `rephrase-${i}` ? "Rephrasing…" : "AI rephrase"}
                          </button>
                        </div>
                      </div>
                      <div>
                        <label className="sr-only" htmlFor={`seq-subject-${i}`}>Step {i + 1} subject</label>
                        <input
                          id={`seq-subject-${i}`}
                          className="input"
                          placeholder="Subject"
                          value={s.subject}
                          onChange={(e) => updateStep(i, { subject: e.target.value })}
                        />
                      </div>
                      <div>
                        <label className="sr-only" htmlFor={`seq-body-${i}`}>Step {i + 1} body HTML</label>
                        <textarea
                          id={`seq-body-${i}`}
                          className="textarea min-h-[120px] font-mono text-xs"
                          placeholder="Body HTML — merge tags: {{name}} {{company}} {{owner}} {{city}} {{pitch}} {{tier}}"
                          value={s.body_html}
                          onChange={(e) => updateStep(i, { body_html: e.target.value })}
                        />
                      </div>
                    </div>
                  </li>
                ))}
              </ol>
            </div>

            {mutationError ? <ErrorState error={mutationError} onRetry={() => { setMutationError(null); void load(); }} /> : null}
            {msg && <p role="status" aria-live="polite" className={`notice ${msgTone}`}>{msg}</p>}

            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-4">
              <button aria-label="Add sequence step"
                type="button"
                className="btn-ghost"
                onClick={() => setSteps((s) => [...s, { subject: "", body_html: "", delay_days: (s[s.length - 1]?.delay_days || 0) + 3 }])}
              >
                <Icon name="plus" size={14} />
                Add step
              </button>
              <button aria-label="Create follow-up sequence" type="button" disabled={busy} onClick={save} className="btn-primary">
                {busy ? "Saving…" : "Create sequence"}
              </button>
            </div>
          </div>
        </Panel>

        <div className="space-y-4">
          <Panel title="How stops work">
            <ul className="space-y-3 text-[0.8125rem] text-ink-muted">
              <li className="flex gap-2.5"><span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-600 dark:bg-emerald-400" aria-hidden="true" />A reply stops the enrollment so nobody gets chased after answering.</li>
              <li className="flex gap-2.5"><span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" aria-hidden="true" />Opt-outs and undeliverable addresses stop immediately.</li>
              <li className="flex gap-2.5"><span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-brand-600 dark:bg-brand-300" aria-hidden="true" />Enroll leads from the Leads screen once a sequence exists.</li>
            </ul>
          </Panel>
          <Panel title="Recent sequences" bodyClassName="p-0">
            {loading && !sequences.length ? <LoadingRows rows={3} cols={2} /> : sequences.length === 0 ? (
              <p className="px-5 py-6 text-[0.8125rem] text-ink-muted">No sequences yet.</p>
            ) : (
              <ul className="divide-y divide-line">
                {sequences.slice(0, 6).map((s) => (
                  <li key={s.id} className="flex items-center justify-between gap-3 px-5 py-3">
                    <div className="min-w-0">
                      <div className="truncate text-[0.8125rem] font-medium text-ink">{s.name}</div>
                      <div className="text-xs tabular-nums text-ink-faint">{s.step_count ?? s.steps?.length ?? "—"} steps</div>
                    </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <StatusBadge status={s.status || "unknown"} />
                        {s.status === "active" ? <button type="button" className="btn-ghost btn-sm" onClick={() => void transition(s, "pause")}>Pause</button> : s.status === "paused" ? <button type="button" className="btn-secondary btn-sm" onClick={() => void transition(s, "resume")}>Resume</button> : null}
                        {s.status !== "completed" ? <button type="button" className="btn-ghost btn-sm" onClick={() => void transition(s, "complete")}>Complete</button> : null}
                        <button type="button" className="btn-ghost btn-sm text-rust-700 dark:text-rust-300" onClick={() => void removeSequence(s)}>Delete</button>
                      </div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>

      <Panel title="All sequences" description="Enrollment outcomes per sequence, as reported by the API." bodyClassName="p-0">
        {loading ? <LoadingRows rows={4} cols={5} /> : loadError ? <div className="p-4"><ErrorState error={loadError} onRetry={load} /></div> : (
        <div className="overflow-x-auto">
          <table className="data-table">
            <caption className="sr-only">Follow-up sequences and enrollment outcomes</caption>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Status</th>
                <th scope="col" className="!text-right">Steps</th>
                <th scope="col" className="!text-right">Active</th>
                <th scope="col" className="!text-right">Completed</th>
                <th scope="col" className="!text-right">Stopped (reply)</th>
                <th scope="col" className="!text-right">Stopped (opt-out)</th>
              </tr>
            </thead>
            <tbody>
              {sequences.map((s) => (
                <tr key={s.id}>
                  <td className="font-medium text-ink">{s.name}</td>
                  <td><div className="flex flex-wrap items-center justify-end gap-2"><StatusBadge status={s.status || "unknown"} />{s.status === "active" ? <button type="button" className="btn-ghost btn-sm" onClick={() => void transition(s, "pause")}>Pause</button> : s.status === "paused" ? <button type="button" className="btn-secondary btn-sm" onClick={() => void transition(s, "resume")}>Resume</button> : null}<button type="button" className="btn-ghost btn-sm" onClick={() => void transition(s, "complete")}>Complete</button><button type="button" className="btn-ghost btn-sm text-rust-700 dark:text-rust-300" onClick={() => void removeSequence(s)}>Delete</button></div></td>
                  <td className="text-right tabular-nums">{s.step_count ?? s.steps?.length ?? "—"}</td>
                  <td className="text-right tabular-nums">{s.enrollment_counts?.active ?? <Missing label="Active" />}</td>
                  <td className="text-right tabular-nums">{s.enrollment_counts?.completed ?? <Missing label="Completed" />}</td>
                  <td className="text-right tabular-nums">{s.enrollment_counts?.stopped_reply ?? <Missing label="Reply stop" />}</td>
                  <td className="text-right tabular-nums">{s.enrollment_counts?.stopped_optout ?? <Missing label="Opt-out stop" />}</td>
                </tr>
              ))}
              {sequences.length === 0 && (
                <tr>
                  <td colSpan={7} className="!p-0"><EmptyState title="No sequences yet" hint="Create a reusable follow-up path above, then enroll selected leads from Leads." /></td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        )}
        {!loading && !loadError && sequences.some((s) => !s.enrollment_counts) ? (
          <div className="border-t border-line p-4"><UnavailableNote label="Enrollment counts">Some sequences did not return enrollment telemetry; cells show a dash rather than zero.</UnavailableNote></div>
        ) : null}
      </Panel>
    </div>
  );
}
