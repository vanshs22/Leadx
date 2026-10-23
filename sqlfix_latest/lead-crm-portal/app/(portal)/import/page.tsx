"use client";

/**
 * Import an existing customer list.
 *
 * Preview first, always: header mapping is guessed from column names, and
 * seeing the guess before 3000 rows land is the difference between a
 * useful import and a cleanup job. Rows go into companies/contacts — the
 * client's own CRM records — never into the delivered-leads pool.
 */
import Link from "next/link";
import { useState } from "react";

import { crmExtras } from "@/lib/api";
import { ErrorState } from "@/components/ui/States";
import { getTenantId } from "@/lib/tenant";

type Entity = "companies" | "contacts";

export default function ImportPage() {
  const [entity, setEntity] = useState<Entity>("companies");
  const [csv, setCsv] = useState("");
  const [fileName, setFileName] = useState("");
  const [prev, setPrev] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function pick(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setFileName(f.name);
    setPrev(null);
    setMsg("");
    setError("");
    const text = await f.text();
    setCsv(text);
  }

  async function doPreview() {
    setBusy(true);
    setError("");
    try {
      setPrev(await crmExtras.importPreview(getTenantId(), csv, entity));
    } catch (e: any) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  async function doImport() {
    setBusy(true);
    setError("");
    try {
      const r = await crmExtras.importRun(getTenantId(), csv, entity);
      setMsg(
        `Queued ${r.rows} rows. You'll get a notification when it finishes — you can leave this page.`
      );
      setPrev(null);
      setCsv("");
      setFileName("");
    } catch (e: any) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <header>
        <h1 className="page-title">Import</h1>
        <p className="page-sub">Bring your existing customer list into this workspace</p>
      </header>

      {error && <ErrorState error={error} onRetry={prev ? doImport : doPreview} />}
      {msg && (
        <div className="card-pad border-l-2 border-l-signal-500">
          <p className="text-sm text-ink">{msg}</p>
          <Link
            href="/notifications"
            className="mt-1 inline-block text-xs font-medium text-brand-600 hover:underline dark:text-brand-300"
          >
            Notifications →
          </Link>
        </div>
      )}

      <div className="card-pad space-y-3">
        <div className="flex flex-wrap gap-2">
          {(["companies", "contacts"] as Entity[]).map((v) => (
            <button aria-label="Action"
              key={v}
              onClick={() => {
                setEntity(v);
                setPrev(null);
              }}
              className={
                entity === v
                  ? "pill bg-brand-600 text-white"
                  : "pill border border-line bg-surface-2 text-ink-muted"
              }
            >
              {v}
            </button>
          ))}
        </div>

        <label className="block text-xs text-ink-muted">
          CSV file
          <input
            type="file"
            accept=".csv,text/csv,text/plain"
            onChange={pick}
            className="mt-1 block w-full text-sm text-ink"
          />
        </label>
        {fileName && (
          <p className="text-xs text-ink-faint">
            {fileName} · {(csv.length / 1024).toFixed(0)} KB
          </p>
        )}

        <p className="text-xs text-ink-faint">
          Common column names are recognised automatically (Company, Business Name, Account, URL,
          Phone, City…). Comma, semicolon and tab separated files all work. Max 5,000 rows per file.
        </p>

        <div className="flex gap-2">
          <button aria-label="Action"
            disabled={!csv || busy}
            onClick={doPreview}
            className="btn-secondary disabled:opacity-40"
          >
            Preview
          </button>
          <button aria-label="Action"
            disabled={!prev?.ok || busy}
            onClick={doImport}
            className="btn-primary disabled:opacity-40"
          >
            Import {prev?.total_rows ? `${prev.total_rows} rows` : ""}
          </button>
        </div>
      </div>

      {prev && (
        <section className="card-pad space-y-3">
          <h2 className="text-sm font-semibold text-ink">Preview</h2>
          {prev.warning && (
            <p className="text-sm text-amber-600 dark:text-amber-300">{prev.warning}</p>
          )}

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <div className="text-xs uppercase tracking-wide text-ink-faint">Mapped columns</div>
              <ul className="mt-1 space-y-0.5 text-sm text-ink">
                {Object.entries(prev.mapped || {}).map(([field, header]) => (
                  <li key={field}>
                    <span className="font-mono text-xs text-ink-faint">{String(header)}</span> →{" "}
                    {field}
                  </li>
                ))}
                {!Object.keys(prev.mapped || {}).length && (
                  <li className="text-ink-faint">Nothing recognised — check the header row.</li>
                )}
              </ul>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wide text-ink-faint">Ignored columns</div>
              <p className="mt-1 text-sm text-ink-muted">
                {(prev.unmapped || []).join(", ") || "none"}
              </p>
            </div>
          </div>

          {!!(prev.sample || []).length && (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="text-ink-faint">
                  <tr>
                    {Object.keys(prev.sample[0]).map((h) => (
                      <th key={h} className="whitespace-nowrap px-2 py-1 font-medium">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="text-ink-muted">
                  {prev.sample.map((r: any, i: number) => (
                    <tr key={i} className="border-t border-line">
                      {Object.keys(prev.sample[0]).map((h) => (
                        <td key={h} className="max-w-[200px] truncate px-2 py-1">
                          {r[h]}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
