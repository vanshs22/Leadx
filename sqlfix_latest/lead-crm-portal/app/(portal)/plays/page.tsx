"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { crm } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { DataState, EmptyState, LoadingRows } from "@/components/ui/States";
import { Notice, PageHeader, Panel, StatusBadge } from "@/components/ui/Primitives";

const SIGNALS: [string, string][] = [["site_redesign", "Website redesigned"], ["contact_change", "Contact details changed"], ["tech_change", "Technology changed"], ["any", "Any signal"]];
const ACTIONS: [string, string][] = [["create_task", "Create a follow-up task"], ["notify_owner", "Notify the workspace owner"]];

export default function PlaysPage() {
  const tid = useMemo(() => getTenantId(), []);
  const [plays, setPlays] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [name, setName] = useState("");
  const [signal, setSignal] = useState("site_redesign");
  const [strength, setStrength] = useState(0.3);
  const [action, setAction] = useState("create_task");
  const [text, setText] = useState("Follow up: {title}");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "success" | "danger"; text: string } | null>(null);

  const load = useCallback(async () => {
    if (!tid) { setLoading(false); setError(new Error("Choose a workspace first.")); return; }
    setLoading(true); setError(null);
    try { const r: any = await crm.automations(tid); setPlays((r.automations || []).filter((a: any) => a.trigger_type === "lead.signal_detected")); }
    catch (e) { setError(e); } finally { setLoading(false); }
  }, [tid]);
  useEffect(() => { void load(); }, [load]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!tid) return;
    setBusy(true); setNote(null);
    const conditions: any[] = [{ field: "strength", op: "gte", value: strength }];
    if (signal !== "any") conditions.unshift({ field: "signal_type", op: "eq", value: signal });
    const act = action === "create_task" ? { type: "create_task", params: { title: text, priority: "high" } } : { type: "notify_owner", params: { message: text } };
    try {
      await crm.createAutomation(tid, { name: name.trim() || `Play: ${SIGNALS.find((s) => s[0] === signal)?.[1]}`, trigger_type: "lead.signal_detected", enabled: true,
        graph: { trigger: { event: "lead.signal_detected", conditions }, actions: [act] } });
      setName(""); setNote({ tone: "success", text: "Play created and active." }); await load();
    } catch (err) { setNote({ tone: "danger", text: err instanceof Error ? err.message : "Could not create the play." }); } finally { setBusy(false); }
  }
  async function toggle(p: any) { if (!tid) return; try { await crm.updateAutomation(tid, p.id, { name: p.name, trigger_type: p.trigger_type, graph: p.graph, enabled: !p.enabled }); await load(); } catch (err) { setNote({ tone: "danger", text: err instanceof Error ? err.message : "Could not update the play." }); } }
  async function remove(p: any) { if (!tid || !window.confirm(`Delete the play "${p.name}"?`)) return; try { await crm.deleteAutomation(tid, p.id); await load(); } catch (err) { setNote({ tone: "danger", text: err instanceof Error ? err.message : "Could not delete the play." }); } }

  return (
    <div className="space-y-4">
      <PageHeader eyebrow="Engage" title="Plays" description="When LeadX detects a change on a lead's website (redesign, new contact details, new technology), run an action automatically. Checks run from a lead's “Check website for changes” button." />
      {note && <Notice tone={note.tone} onDismiss={() => setNote(null)}>{note.text}</Notice>}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        <Panel title="Your plays" description="Plays are stored as automations and appear on the Automations page too.">
          <DataState loading={loading} error={error} onRetry={load} skeleton={<LoadingRows />} isEmpty={plays.length === 0} empty={<EmptyState title="No plays yet" hint="Create one on the right, for example: when a website is redesigned, create a follow-up task." />}>
            <ul className="divide-y divide-line">
              {plays.map((p) => {
                const c: any[] = p.graph?.trigger?.conditions || [];
                const sig = c.find((x) => x.field === "signal_type")?.value;
                return (
                  <li key={p.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                    <div><div className="font-medium">{p.name}</div>
                      <div className="text-xs text-ink-faint">When: {SIGNALS.find((s) => s[0] === (sig || "any"))?.[1]} → {(p.graph?.actions || []).map((a: any) => ACTIONS.find((x) => x[0] === a.type)?.[1] || a.type).join(", ")}</div></div>
                    <div className="flex items-center gap-2"><StatusBadge status={p.enabled ? "active" : "paused"} />
                      <button type="button" className="btn-secondary h-8" onClick={() => toggle(p)}>{p.enabled ? "Pause" : "Resume"}</button>
                      <button type="button" className="btn-secondary h-8" onClick={() => remove(p)}>Delete</button></div>
                  </li>
                );
              })}
            </ul>
          </DataState>
        </Panel>
        <Panel title="New play" description="Pick the signal and what should happen.">
          <form onSubmit={create} className="space-y-3">
            <div><label htmlFor="play-name" className="field-label">Name (optional)</label><input id="play-name" className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} /></div>
            <div><label htmlFor="play-signal" className="field-label">When this signal appears</label><select id="play-signal" className="select" value={signal} onChange={(e) => setSignal(e.target.value)}>{SIGNALS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
            <div><label htmlFor="play-strength" className="field-label">Minimum strength: {strength.toFixed(1)}</label><input id="play-strength" className="w-full" type="range" min={0} max={1} step={0.1} value={strength} onChange={(e) => setStrength(Number(e.target.value))} /></div>
            <div><label htmlFor="play-action" className="field-label">Then</label><select id="play-action" className="select" value={action} onChange={(e) => setAction(e.target.value)}>{ACTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
            <div><label htmlFor="play-text" className="field-label">{action === "create_task" ? "Task title" : "Message"}</label><input id="play-text" className="input" value={text} onChange={(e) => setText(e.target.value)} maxLength={200} required /><p className="field-hint">{"{title}"} is replaced with the signal and company name.</p></div>
            <button className="btn-primary" disabled={busy}>{busy ? "Creating…" : "Create play"}</button>
          </form>
        </Panel>
      </div>
    </div>
  );
}
