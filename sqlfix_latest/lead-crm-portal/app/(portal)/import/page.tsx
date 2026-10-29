"use client";

import Link from "next/link";
import { useState } from "react";
import { crmExtras } from "@/lib/api";
import { Badge, Notice, PageHeader, Panel } from "@/components/ui/Primitives";
import { EmptyState, ErrorState } from "@/components/ui/States";
import { Icon } from "@/components/ui/Icon";
import { getTenantId } from "@/lib/tenant";

type Entity = "companies" | "contacts";
export default function ImportPage() {
  const [entity, setEntity] = useState<Entity>("companies"), [csv, setCsv] = useState(""), [fileName, setFileName] = useState(""), [prev, setPrev] = useState<any>(null), [msg, setMsg] = useState(""), [error, setError] = useState<unknown>(null), [busy, setBusy] = useState(false);
  async function pick(e: React.ChangeEvent<HTMLInputElement>) { const f = e.target.files?.[0]; if (!f) return; setFileName(f.name); setPrev(null); setMsg(""); setError(null); setCsv(await f.text()); }
  async function doPreview() { setBusy(true); setError(null); try { setPrev(await crmExtras.importPreview(getTenantId(), csv, entity)); } catch (caught) { setError(caught); } finally { setBusy(false); } }
  async function doImport() { setBusy(true); setError(null); try { const r = await crmExtras.importRun(getTenantId(), csv, entity); setMsg(`Queued ${r.rows} rows. You’ll get a notification when it finishes.`); setPrev(null); setCsv(""); setFileName(""); } catch (caught) { setError(caught); } finally { setBusy(false); } }
  // Visual step only — derived from existing state, no new state machine.
  const current = prev ? 2 : csv ? 1 : 0;
  const steps = ["Upload", "Map columns", "Review & import"];
  const mappedEntries = prev ? Object.entries(prev.mapped || {}) : [];
  return (
    <div className="page">
      <PageHeader eyebrow="Data" title="Import" description="Bring an existing customer list into this workspace." />
      <ol className="grid gap-2 sm:grid-cols-3" aria-label="Import steps">
        {steps.map((label, i) => {
          const done = i < current, active = i === current;
          return (
            <li key={label} aria-current={active ? "step" : undefined} className={`flex items-center gap-3 rounded-lg border px-3 py-2.5 ${active ? "border-brand-300 bg-brand-50 dark:border-brand-500/40 dark:bg-brand-500/10" : "border-line bg-surface-2"}`}>
              <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold tabular-nums ${done ? "bg-emerald-600 text-white dark:bg-emerald-400 dark:text-emerald-950" : active ? "bg-brand-700 text-white dark:bg-brand-300 dark:text-brand-900" : "border border-line-strong text-ink-faint"}`} aria-hidden="true">{done ? <Icon name="check" size={13} /> : i + 1}</span>
              <span className={`text-[0.8125rem] font-medium ${active ? "text-ink" : done ? "text-ink-muted" : "text-ink-faint"}`}>{label}<span className="sr-only">{done ? " (complete)" : active ? " (current)" : ""}</span></span>
            </li>
          );
        })}
      </ol>
      {error ? <ErrorState error={error} onRetry={() => void (prev ? doImport() : doPreview())} /> : null}
      {msg ? <Notice tone="success" title="Import queued">{msg} <Link href="/notifications" className="link ml-1">Open notifications</Link></Notice> : null}

      <Panel
        title="1. Upload a CSV"
        description="Choose what you are importing, then pick a file."
        footer={<><span>Common column names are recognised automatically. Max 5,000 rows per file.</span><button disabled={!csv || busy} onClick={() => void doPreview()} className="btn-secondary shrink-0">{busy && !prev ? "Working…" : "Preview"}</button></>}
      >
        <div className="grid gap-5 md:grid-cols-2">
          <div>
            <span className="field-label" id="import-entity-label">Record type</span>
            <div className="tabs" role="group" aria-label="Import entity">
              <button type="button" aria-pressed={entity === "companies"} onClick={() => { setEntity("companies"); setPrev(null); }} className={entity === "companies" ? "tab-active" : "tab"}>Companies</button>
              <button type="button" aria-pressed={entity === "contacts"} onClick={() => { setEntity("contacts"); setPrev(null); }} className={entity === "contacts" ? "tab-active" : "tab"}>Contacts</button>
            </div>
          </div>
          <div>
            <label htmlFor="import-csv-file" className="field-label">CSV file</label>
            <input id="import-csv-file" aria-label="CSV file" type="file" accept=".csv,text/csv,text/plain" onChange={pick} className="block w-full rounded-lg border border-dashed border-line-strong bg-surface-3/50 px-3 py-2 text-[0.8125rem] text-ink file:mr-3 file:rounded-md file:border-0 file:bg-surface-2 file:px-2.5 file:py-1 file:text-xs file:font-semibold file:text-ink" />
            {fileName ? <p className="field-hint"><span className="font-mono">{fileName}</span> · <span className="tabular-nums">{(csv.length / 1024).toFixed(0)} KB</span></p> : <p className="field-hint">.csv or plain text with a header row.</p>}
          </div>
        </div>
      </Panel>

      {prev ? <>
        <Panel title="2. Map columns" description="How your header row lines up with LeadX fields.">
          {prev.warning ? <div className="mb-4"><Notice tone="warning">{prev.warning}</Notice></div> : null}
          <div className="grid gap-5 md:grid-cols-2">
            <div>
              <div className="label">Mapped columns</div>
              {mappedEntries.length ? (
                <ul className="mt-2 divide-y divide-line rounded-lg border border-line">
                  {mappedEntries.map(([field, header]) => <li key={field} className="flex items-center justify-between gap-3 px-3 py-2 text-[0.8125rem]"><span className="truncate font-mono text-xs text-ink-muted">{String(header)}</span><Icon name="arrow" size={13} className="shrink-0 text-ink-faint" aria-hidden="true" /><span className="truncate font-medium text-ink">{field}</span></li>)}
                </ul>
              ) : <p className="mt-2 text-[0.8125rem] text-ink-faint">Nothing recognised — check the header row.</p>}
            </div>
            <div>
              <div className="label">Ignored columns</div>
              <div className="mt-2 flex flex-wrap gap-1.5">{(prev.unmapped || []).length ? (prev.unmapped || []).map((h: string) => <Badge key={h} tone="neutral" className="font-mono">{h}</Badge>) : <span className="text-[0.8125rem] text-ink-muted">none</span>}</div>
            </div>
          </div>
        </Panel>
        <Panel
          title="3. Review & import"
          description="First rows of the file as LeadX will read them."
          bodyClassName={(prev.sample || []).length ? "p-0" : "panel-body"}
          footer={<><span className="tabular-nums">{prev?.total_rows ? `${prev.total_rows} rows ready` : "Row count unavailable"}</span><button disabled={!prev?.ok || busy} onClick={() => void doImport()} className="btn-primary shrink-0">{busy ? "Working…" : `Import ${prev?.total_rows ? `${prev.total_rows} rows` : ""}`}</button></>}
        >
          {(prev.sample || []).length ? <div className="overflow-x-auto"><table className="data-table"><caption className="sr-only">Import preview rows</caption><thead><tr>{Object.keys(prev.sample[0]).map((h) => <th key={h} className="whitespace-nowrap">{h}</th>)}</tr></thead><tbody>{prev.sample.map((r: any, i: number) => <tr key={i}>{Object.keys(prev.sample[0]).map((h) => <td key={h} className="max-w-[200px] truncate">{r[h]}</td>)}</tr>)}</tbody></table></div> : <EmptyState title="No sample rows" hint="The preview returned no rows to show." />}
        </Panel>
      </> : null}
    </div>
  );
}
