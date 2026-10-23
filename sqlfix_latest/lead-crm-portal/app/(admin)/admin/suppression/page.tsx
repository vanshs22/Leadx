"use client";

import { useEffect, useState } from "react";
import { admin } from "@/lib/admin";

export default function AdminSuppressionPage() {
  const [tenants, setTenants] = useState<any[]>([]);
  const [tenantId, setTenantId] = useState("");
  const [csv, setCsv] = useState("");
  const [optOut, setOptOut] = useState({
    company_name: "",
    phone: "",
    email: "",
    website: "",
    reason: "requested",
  });
  const [msg, setMsg] = useState("");

  useEffect(() => {
    admin.tenants().then((d) => setTenants(d.tenants || []));
  }, []);

  function parseRows(text: string) {
    const lines = text.trim().split(/\r?\n/).filter(Boolean);
    if (lines.length === 0) return [];
    const headers = lines[0].split(",").map((h) => h.trim().toLowerCase());
    return lines.slice(1).map((line) => {
      const cols = line.split(",").map((c) => c.trim());
      const row: Record<string, string> = {};
      headers.forEach((h, i) => {
        row[h] = cols[i] || "";
      });
      return {
        company_name: row.company || row.company_name || row.name || "",
        phone: row.phone || row.phone_number || "",
        email: row.email || "",
        website: row.website || row.domain || "",
      };
    });
  }

  async function upload() {
    try {
      const rows = parseRows(csv);
      if (!rows.length) {
        setMsg("Paste CSV with header row");
        return;
      }
      const r = await admin.suppressionUpload(tenantId, rows);
      setMsg(`Uploaded: ${JSON.stringify(r)}`);
      setCsv("");
    } catch (e: any) {
      setMsg(e.message);
    }
  }

  async function doOptOut() {
    try {
      const r = await admin.optOut(optOut);
      setMsg(`Opt-out: ${JSON.stringify(r)}`);
      setOptOut({ company_name: "", phone: "", email: "", website: "", reason: "requested" });
    } catch (e: any) {
      setMsg(e.message);
    }
  }

  return (
    <div className="space-y-8 p-8">
      <header>
        <h1 className="text-2xl font-semibold text-ink">Suppression & opt-out</h1>
        <p className="text-sm text-ink-faint">
          Never resell client CRM contacts · global blocklist
        </p>
      </header>

      {msg && <p className="text-sm text-brand-300 break-all">{msg}</p>}

      <section className="space-y-3 premium-card p-5">
        <h2 className="text-sm font-semibold text-ink">Client suppression upload</h2>
        <select
          className="w-full max-w-md rounded-lg border border-line bg-surface px-3 py-2 text-sm"
          value={tenantId}
          onChange={(e) => setTenantId(e.target.value)}
        >
          <option value="">Select client…</option>
          {tenants.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
        <textarea
          placeholder={"company,phone,email,website\nGlow Spa,2015550100,a@x.com,glow.com"}
          className="h-40 w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-xs"
          value={csv}
          onChange={(e) => setCsv(e.target.value)}
        />
        <button aria-label="Action"
          onClick={upload}
          disabled={!tenantId || !csv}
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm text-white disabled:opacity-50"
        >
          Upload suppression list
        </button>
      </section>

      <section className="space-y-3 premium-card p-5">
        <h2 className="text-sm font-semibold text-ink">Global opt-out</h2>
        <p className="text-xs text-ink-faint">Blocks this business for every client forever</p>
        <div className="grid gap-2 sm:grid-cols-2">
          {(["company_name", "phone", "email", "website"] as const).map((k) => (
            <input
              key={k}
              placeholder={k}
              className="rounded-lg border border-line bg-surface px-3 py-2 text-sm"
              value={optOut[k]}
              onChange={(e) => setOptOut({ ...optOut, [k]: e.target.value })}
            />
          ))}
        </div>
        <button aria-label="Action" onClick={doOptOut} className="rounded-lg border border-line px-4 py-2 text-sm text-ink-muted">
          Add global opt-out
        </button>
      </section>
    </div>
  );
}
