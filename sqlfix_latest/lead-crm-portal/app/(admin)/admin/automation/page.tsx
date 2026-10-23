"use client";

/**
 * Automation — recurring discovery wired straight into outreach.
 *
 * Everything here already existed as pieces: pipeline.run found leads,
 * email_sequences sent follow-ups, and the worker processed due sends. What was
 * missing was the trigger and the join. A schedule row supplies both: the worker
 * enqueues its pipeline run on a cadence, and auto_enroll_sequence_id drops the
 * newly assigned leads into a cadence with no human step in between.
 *
 * Sequences are built here too, because sequence CRUD used to live only on the
 * tenant-authenticated /crm router — an admin literally could not create the
 * cadence they were being asked to schedule into.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  adminExtras,
  admin,
  cadenceLabel,
  CADENCE_OPTIONS,
  type Schedule,
  type Sequence,
  type SequenceStepInput,
} from "@/lib/admin";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/States";

type Tenant = { id: string; name: string; is_operator?: boolean };
type Icp = { id: string; name: string; vertical?: string; geo?: string[] };

const csv = (s: string) =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

/** Steps a first-time user can send without writing anything: a touch, a nudge
 *  three days later, a final one a week in. Merge fields match the sequence
 *  renderer's placeholders. */
const STARTER_STEPS: SequenceStepInput[] = [
  {
    step_number: 1,
    delay_days: 0,
    subject: "Quick question about {{company}}",
    body_html:
      "<p>Hi {{first_name}},</p><p>I came across {{company}} and had one quick question — are you currently taking on new work?</p><p>Happy to share what we're seeing in your market either way.</p>",
  },
  {
    step_number: 2,
    delay_days: 3,
    subject: "Re: {{company}}",
    body_html:
      "<p>Hi {{first_name}},</p><p>Following up on the note below in case it slipped past. Worth a short conversation?</p>",
  },
  {
    step_number: 3,
    delay_days: 7,
    subject: "Closing the loop",
    body_html:
      "<p>Hi {{first_name}},</p><p>I'll stop here so I'm not cluttering your inbox. If the timing changes, just reply to this and I'll pick it back up.</p>",
  },
];

export default function AdminAutomationPage() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantId, setTenantId] = useState("");
  const [rows, setRows] = useState<Schedule[]>([]);
  const [sequences, setSequences] = useState<Sequence[]>([]);
  const [icps, setIcps] = useState<Icp[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const [formOpen, setFormOpen] = useState(false);
  const emptyForm = {
    name: "",
    icp_id: "",
    industry: "",
    locations: "",
    queries: "",
    target_new_leads: 25,
    interval_hours: 24,
    auto_enroll_sequence_id: "",
  };
  const [form, setForm] = useState(emptyForm);

  const [seqOpen, setSeqOpen] = useState(false);
  const [seqName, setSeqName] = useState("");
  const [steps, setSteps] = useState<SequenceStepInput[]>(STARTER_STEPS);

  // Clients plus our own internal workspace: automation applies to both, and
  // "run our prospecting nightly" is the same feature as a client's schedule.
  useEffect(() => {
    (async () => {
      try {
        const [t, w] = await Promise.all([
          admin.tenants(undefined, 200),
          adminExtras.myWorkspace().catch(() => null),
        ]);
        const list: Tenant[] = t?.tenants || t?.rows || [];
        const withOps = w?.tenant_id
          ? [{ id: w.tenant_id, name: `${w.tenant_name} (us)`, is_operator: true }, ...list.filter((x) => x.id !== w.tenant_id)]
          : list;
        setTenants(withOps);
        setTenantId((cur) => cur || withOps[0]?.id || "");
      } catch (e) {
        setLoadError(e);
        setLoading(false);
      }
    })();
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const r = await adminExtras.schedules(tenantId || undefined);
      setRows(r.rows || []);
    } catch (e) {
      setLoadError(e);
    } finally {
      setLoading(false);
    }
  }, [tenantId]);

  useEffect(() => {
    load();
  }, [load]);

  // Sequence + ICP dropdowns are per-tenant: enrolling a client's lead into
  // another client's sequence would send the wrong email from the wrong domain.
  useEffect(() => {
    if (!tenantId) return;
    let cancelled = false;
    Promise.all([
      adminExtras.sequences(tenantId).catch(() => ({ rows: [] as Sequence[] })),
      admin.icps(tenantId).catch(() => null),
    ]).then(([s, i]) => {
      if (cancelled) return;
      setSequences(s.rows || []);
      setIcps(i?.icps || i?.rows || []);
    });
    return () => {
      cancelled = true;
    };
  }, [tenantId]);

  const seqName_ = useMemo(
    () => (id: string | null) => sequences.find((s) => s.id === id)?.name || "",
    [sequences]
  );

  async function act(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    setMsg("");
    try {
      const r: any = await fn();
      setMsg(r?.error ? r.error : ok);
      await load();
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!tenantId) return setMsg("Pick a client first.");
    if (!form.name.trim()) return setMsg("Give the automation a name.");
    if (!form.icp_id && !form.industry.trim())
      return setMsg("Choose an ICP, or type an industry to search for.");
    await act(
      () =>
        adminExtras.createSchedule({
          tenant_id: tenantId,
          name: form.name.trim(),
          icp_id: form.icp_id || null,
          industry: form.industry.trim() || null,
          locations: csv(form.locations),
          queries: csv(form.queries),
          target_new_leads: form.target_new_leads,
          interval_hours: form.interval_hours,
          auto_enroll_sequence_id: form.auto_enroll_sequence_id || null,
          active: true,
        }),
      "Automation created — first run fires within 5 minutes."
    );
    setFormOpen(false);
    setForm(emptyForm);
  }

  async function handleCreateSequence(e: React.FormEvent) {
    e.preventDefault();
    if (!tenantId) return setMsg("Pick a client first.");
    if (!seqName.trim()) return setMsg("Name the sequence.");
    setBusy(true);
    try {
      await adminExtras.createSequence({
        tenant_id: tenantId,
        name: seqName.trim(),
        steps: steps.map((s, i) => ({ ...s, step_number: i + 1 })),
      });
      const s = await adminExtras.sequences(tenantId);
      setSequences(s.rows || []);
      setMsg(`Sequence "${seqName.trim()}" created.`);
      setSeqOpen(false);
      setSeqName("");
      setSteps(STARTER_STEPS);
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="page-title">Automation</h1>
          <p className="page-sub">
            Find leads on a schedule and start the emails automatically — for a client, or for us.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn-primary" onClick={() => setFormOpen(true)}>
            New automation
          </button>
          <button type="button" className="btn-secondary" onClick={() => setSeqOpen(true)}>
            New sequence
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={busy}
            onClick={() => act(() => adminExtras.tickSchedules(), "Checked all schedules.")}
          >
            Run due now
          </button>
        </div>
      </header>

      {msg && (
        <div className="rounded-xl border border-line bg-surface-2 px-4 py-2.5 text-sm text-ink-muted">
          {msg}
        </div>
      )}

      <div className="card-pad flex flex-wrap items-end gap-3">
        <div className="min-w-[240px] flex-1">
          <label className="label" htmlFor="auto-tenant">
            Client
          </label>
          <select
            id="auto-tenant"
            className="input mt-1"
            value={tenantId}
            onChange={(e) => setTenantId(e.target.value)}
          >
            {tenants.length === 0 && <option value="">No clients yet</option>}
            {tenants.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </div>
        <p className="max-w-md text-xs text-ink-faint">
          The worker checks for due automations every 5 minutes. Leads that pass the quality gate are
          assigned to this client, then enrolled in the sequence you pick — follow-ups stop by
          themselves on a reply or an unsubscribe.
        </p>
      </div>

      {loading ? (
        <LoadingRows rows={4} cols={6} />
      ) : loadError ? (
        <ErrorState error={loadError} onRetry={load} />
      ) : rows.length === 0 ? (
        <EmptyState
          title="No automations yet"
          hint="Create one to have leads found and emailed on a cadence instead of by hand."
        />
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[900px] text-sm">
            <caption className="sr-only">Scheduled lead-finding automations</caption>
            <thead>
              <tr className="border-b border-line text-left">
                <th scope="col" className="label px-4 py-3">
                  Name
                </th>
                <th scope="col" className="label px-4 py-3">
                  Searching for
                </th>
                <th scope="col" className="label px-4 py-3">
                  Cadence
                </th>
                <th scope="col" className="label px-4 py-3">
                  Then emails
                </th>
                <th scope="col" className="label px-4 py-3">
                  Last run
                </th>
                <th scope="col" className="label px-4 py-3">
                  Next run
                </th>
                <th scope="col" className="label px-4 py-3 text-right">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.id} className="border-b border-line/60 hover:bg-surface-3/50">
                  <td className="px-4 py-3">
                    <span className="font-medium text-ink">{s.name}</span>
                    <span
                      className={
                        s.active
                          ? "pill ml-2 bg-signal-50 text-signal-700 dark:bg-signal-500/15 dark:text-signal-300"
                          : "pill ml-2 bg-surface-3 text-ink-faint"
                      }
                    >
                      {s.active ? "Active" : "Paused"}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-ink-muted">
                    {s.industry || icps.find((i) => i.id === s.icp_id)?.name || "—"}
                    {s.locations?.length ? (
                      <span className="block text-xs text-ink-faint">
                        {s.locations.slice(0, 3).join(", ")}
                        {s.locations.length > 3 ? ` +${s.locations.length - 3}` : ""}
                      </span>
                    ) : null}
                  </td>
                  <td className="px-4 py-3 text-ink-muted">
                    {cadenceLabel(s.interval_hours)}
                    <span className="block text-xs text-ink-faint">
                      up to {s.target_new_leads} leads
                    </span>
                  </td>
                  <td className="px-4 py-3 text-ink-muted">
                    {s.auto_enroll_sequence_id ? (
                      seqName_(s.auto_enroll_sequence_id) || "sequence"
                    ) : (
                      <span className="text-ink-faint">Discover only</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-ink-muted">
                    {s.last_run_at ? (
                      <>
                        {new Date(s.last_run_at).toLocaleString()}
                        <span className="block text-xs text-ink-faint">
                          {s.last_result?.assigned_to_tenant != null
                            ? `${s.last_result.assigned_to_tenant} assigned of ${s.last_result.discovered ?? "?"} found`
                            : s.last_status || ""}
                        </span>
                      </>
                    ) : (
                      <span className="text-ink-faint">Never</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-ink-muted">
                    {s.active ? new Date(s.next_run_at).toLocaleString() : <span className="text-ink-faint">—</span>}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-2">
                      <button
                        type="button"
                        className="btn-ghost text-xs"
                        disabled={busy}
                        onClick={() =>
                          act(() => adminExtras.runScheduleNow(s.id), `Queued a run for "${s.name}".`)
                        }
                      >
                        Run now
                      </button>
                      <button
                        type="button"
                        className="btn-ghost text-xs"
                        disabled={busy}
                        onClick={() =>
                          act(
                            () => adminExtras.updateSchedule(s.id, { active: !s.active }),
                            s.active ? `Paused "${s.name}".` : `Resumed "${s.name}".`
                          )
                        }
                      >
                        {s.active ? "Pause" : "Resume"}
                      </button>
                      <button
                        type="button"
                        className="btn-ghost text-xs text-danger"
                        disabled={busy}
                        onClick={() => {
                          if (!window.confirm(`Delete automation "${s.name}"? Leads already found are kept.`))
                            return;
                          act(() => adminExtras.deleteSchedule(s.id), `Deleted "${s.name}".`);
                        }}
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {formOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="new-automation-title"
        >
          <form
            onSubmit={handleCreate}
            className="card-pad max-h-[90vh] w-full max-w-xl space-y-3 overflow-y-auto shadow-lift"
          >
            <div className="flex items-center justify-between">
              <h2 id="new-automation-title" className="text-lg font-semibold text-ink">
                New automation
              </h2>
              <button type="button" className="btn-ghost" onClick={() => setFormOpen(false)}>
                Close
              </button>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="sm:col-span-2">
                <label className="label" htmlFor="au-name">
                  Name *
                </label>
                <input
                  id="au-name"
                  className="input mt-1"
                  required
                  placeholder="NJ medspas — nightly"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </div>

              <div>
                <label className="label" htmlFor="au-icp">
                  Use an ICP
                </label>
                <select
                  id="au-icp"
                  className="input mt-1"
                  value={form.icp_id}
                  onChange={(e) => setForm({ ...form, icp_id: e.target.value })}
                >
                  <option value="">No ICP — set it manually</option>
                  {icps.map((i) => (
                    <option key={i.id} value={i.id}>
                      {i.name}
                      {i.vertical ? ` · ${i.vertical}` : ""}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="label" htmlFor="au-industry">
                  …or industry
                </label>
                <input
                  id="au-industry"
                  className="input mt-1"
                  placeholder="medspa"
                  disabled={!!form.icp_id}
                  value={form.industry}
                  onChange={(e) => setForm({ ...form, industry: e.target.value })}
                />
              </div>

              <div className="sm:col-span-2">
                <label className="label" htmlFor="au-locations">
                  Locations
                </label>
                <input
                  id="au-locations"
                  className="input mt-1"
                  placeholder="New Jersey, Philadelphia"
                  value={form.locations}
                  onChange={(e) => setForm({ ...form, locations: e.target.value })}
                />
                <p className="mt-1 text-xs text-ink-faint">
                  Comma-separated. Left empty, the ICP&apos;s own geo is used.
                </p>
              </div>

              <div className="sm:col-span-2">
                <label className="label" htmlFor="au-queries">
                  Extra search terms
                </label>
                <input
                  id="au-queries"
                  className="input mt-1"
                  placeholder="botox clinic, laser hair removal"
                  value={form.queries}
                  onChange={(e) => setForm({ ...form, queries: e.target.value })}
                />
              </div>

              <div>
                <label className="label" htmlFor="au-cadence">
                  How often
                </label>
                <select
                  id="au-cadence"
                  className="input mt-1"
                  value={form.interval_hours}
                  onChange={(e) => setForm({ ...form, interval_hours: Number(e.target.value) })}
                >
                  {CADENCE_OPTIONS.map((c) => (
                    <option key={c.hours} value={c.hours}>
                      {c.label}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="label" htmlFor="au-target">
                  New leads per run
                </label>
                <input
                  id="au-target"
                  type="number"
                  min={1}
                  max={5000}
                  className="input mt-1"
                  value={form.target_new_leads}
                  onChange={(e) =>
                    setForm({ ...form, target_new_leads: Number(e.target.value) || 25 })
                  }
                />
              </div>

              <div className="sm:col-span-2">
                <label className="label" htmlFor="au-seq">
                  Then email them with
                </label>
                <select
                  id="au-seq"
                  className="input mt-1"
                  value={form.auto_enroll_sequence_id}
                  onChange={(e) => setForm({ ...form, auto_enroll_sequence_id: e.target.value })}
                >
                  <option value="">Nothing — just put them in the CRM</option>
                  {sequences.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                      {s.steps?.length ? ` · ${s.steps.length} steps` : ""}
                    </option>
                  ))}
                </select>
                <p className="mt-1 text-xs text-ink-faint">
                  {sequences.length === 0
                    ? "No sequences for this client yet — create one with “New sequence”."
                    : "Sending still respects business hours, the touch gap, domain warm-up caps and opt-outs."}
                </p>
              </div>
            </div>

            <div className="flex gap-2">
              <button type="submit" disabled={busy} className="btn-primary">
                {busy ? "Saving…" : "Create automation"}
              </button>
              <button type="button" className="btn-secondary" onClick={() => setFormOpen(false)}>
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {seqOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="new-sequence-title"
        >
          <form
            onSubmit={handleCreateSequence}
            className="card-pad max-h-[90vh] w-full max-w-2xl space-y-3 overflow-y-auto shadow-lift"
          >
            <div className="flex items-center justify-between">
              <h2 id="new-sequence-title" className="text-lg font-semibold text-ink">
                New sequence
              </h2>
              <button type="button" className="btn-ghost" onClick={() => setSeqOpen(false)}>
                Close
              </button>
            </div>

            <div>
              <label className="label" htmlFor="sq-name">
                Name *
              </label>
              <input
                id="sq-name"
                className="input mt-1"
                required
                placeholder="Cold outreach — 3 touches"
                value={seqName}
                onChange={(e) => setSeqName(e.target.value)}
              />
            </div>

            <p className="text-xs text-ink-faint">
              {"{{first_name}}"} and {"{{company}}"} are filled in per lead. Step 1 sends on
              enrolment; later steps wait the given number of days and are skipped entirely once the
              lead replies or unsubscribes.
            </p>

            {steps.map((st, idx) => (
              <div key={idx} className="rounded-xl border border-line p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="label">Step {idx + 1}</span>
                  {steps.length > 1 && (
                    <button
                      type="button"
                      className="btn-ghost text-xs text-danger"
                      onClick={() => setSteps(steps.filter((_, i) => i !== idx))}
                    >
                      Remove
                    </button>
                  )}
                </div>
                <div className="mt-2 grid gap-2 sm:grid-cols-4">
                  <div>
                    <label className="label" htmlFor={`sq-delay-${idx}`}>
                      Wait (days)
                    </label>
                    <input
                      id={`sq-delay-${idx}`}
                      type="number"
                      min={0}
                      max={180}
                      className="input mt-1"
                      value={st.delay_days}
                      onChange={(e) =>
                        setSteps(
                          steps.map((s, i) =>
                            i === idx ? { ...s, delay_days: Number(e.target.value) || 0 } : s
                          )
                        )
                      }
                    />
                  </div>
                  <div className="sm:col-span-3">
                    <label className="label" htmlFor={`sq-subject-${idx}`}>
                      Subject
                    </label>
                    <input
                      id={`sq-subject-${idx}`}
                      className="input mt-1"
                      required
                      value={st.subject}
                      onChange={(e) =>
                        setSteps(steps.map((s, i) => (i === idx ? { ...s, subject: e.target.value } : s)))
                      }
                    />
                  </div>
                </div>
                <div className="mt-2">
                  <label className="label" htmlFor={`sq-body-${idx}`}>
                    Body
                  </label>
                  <textarea
                    id={`sq-body-${idx}`}
                    className="input mt-1 min-h-[100px] font-mono text-xs"
                    required
                    value={st.body_html}
                    onChange={(e) =>
                      setSteps(steps.map((s, i) => (i === idx ? { ...s, body_html: e.target.value } : s)))
                    }
                  />
                </div>
              </div>
            ))}

            <button
              type="button"
              className="btn-secondary"
              disabled={steps.length >= 20}
              onClick={() =>
                setSteps([
                  ...steps,
                  {
                    step_number: steps.length + 1,
                    delay_days: 3,
                    subject: "Re: {{company}}",
                    body_html: "<p>Hi {{first_name}},</p><p></p>",
                  },
                ])
              }
            >
              Add step
            </button>

            <div className="flex gap-2">
              <button type="submit" disabled={busy} className="btn-primary">
                {busy ? "Saving…" : "Create sequence"}
              </button>
              <button type="button" className="btn-secondary" onClick={() => setSeqOpen(false)}>
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
