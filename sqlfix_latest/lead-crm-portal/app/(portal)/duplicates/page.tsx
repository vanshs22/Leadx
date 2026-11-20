"use client";

import { useCallback, useEffect, useState } from "react";
import { crmTools } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { Notice, PageHeader, Panel } from "@/components/ui/Primitives";
import { DataState, EmptyState, LoadingRows } from "@/components/ui/States";

type Entity = "companies" | "contacts";
type Group = { suggested_keep: string; reasons: string[]; records: Record<string, any>[] };
const label = (r: Record<string, any>) => r.name || r.full_name || r.email || r.id;

export default function DuplicatesPage() {
  const tid = getTenantId();
  const [entity, setEntity] = useState<Entity>("companies");
  const [groups, setGroups] = useState<Group[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "success" | "danger"; text: string } | null>(null);

  const refresh = useCallback(async () => {
    if (!tid) { setLoading(false); setError(new Error("Choose a workspace first.")); return; }
    setLoading(true); setError(null);
    try { setGroups((await crmTools.duplicates(tid, entity)).groups); } catch (e) { setError(e); } finally { setLoading(false); }
  }, [tid, entity]);
  useEffect(() => { void refresh(); }, [refresh]);

  async function merge(g: Group) {
    const others = g.records.filter((r) => r.id !== g.suggested_keep).map((r) => r.id as string);
    if (!window.confirm(`Merge ${others.length} record(s) into the kept record? Related deals, notes and tasks move with it. This cannot be undone.`)) return;
    setBusy(g.suggested_keep); setMessage(null);
    try { await crmTools.merge(tid!, entity, g.suggested_keep, others); setMessage({ tone: "success", text: "Merged." }); await refresh(); }
    catch (e) { setMessage({ tone: "danger", text: e instanceof Error ? e.message : "Merge failed." }); }
    finally { setBusy(null); }
  }

  return (
    <div className="space-y-4">
      <PageHeader title="Duplicates" description="Records that share a domain, email, phone or near-identical name. Merging keeps the most complete record and re-points its deals, notes, tasks and activities." />
      {message && <Notice tone={message.tone} onDismiss={() => setMessage(null)}>{message.text}</Notice>}
      <div role="tablist" aria-label="Record type" className="flex gap-2">
        {(["companies", "contacts"] as Entity[]).map((e) => (
          <button key={e} role="tab" aria-selected={entity === e} className={`btn h-9 ${entity === e ? "btn-primary" : "btn-secondary"}`} onClick={() => setEntity(e)}>{e === "companies" ? "Accounts" : "Contacts"}</button>
        ))}
      </div>
      <DataState loading={loading} error={error} onRetry={refresh} skeleton={<LoadingRows />} isEmpty={groups.length === 0} empty={<EmptyState title="No duplicates found" hint="Nothing in this workspace looks like a duplicate right now." />}>
        {groups.map((g) => (
          <Panel key={g.suggested_keep} title={`${g.records.length} records · matched on ${g.reasons.join(", ")}`}>
            <ul className="space-y-1 text-sm">
              {g.records.map((r) => (
                <li key={r.id} className="flex items-center justify-between gap-3">
                  <span>{label(r)}{r.domain ? ` · ${r.domain}` : ""}{r.email ? ` · ${r.email}` : ""}</span>
                  {r.id === g.suggested_keep && <span className="text-xs text-ink-faint">will be kept</span>}
                </li>
              ))}
            </ul>
            <button className="btn btn-primary mt-3 h-9" disabled={busy === g.suggested_keep} onClick={() => merge(g)}>{busy === g.suggested_keep ? "Merging…" : "Merge into kept record"}</button>
          </Panel>
        ))}
      </DataState>
    </div>
  );
}
