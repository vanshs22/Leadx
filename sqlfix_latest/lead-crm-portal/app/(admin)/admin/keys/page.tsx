"use client";

import { useEffect, useState } from "react";
import { admin } from "@/lib/admin";

export default function AdminKeysPage() {
  const [keys, setKeys] = useState<any[]>([]);
  const [health, setHealth] = useState<any>(null);
  const [form, setForm] = useState({
    provider: "serper",
    key_value: "",
    label: "primary",
    credits_limit: "",
  });
  const [msg, setMsg] = useState("");

  function load() {
    admin.keys().then((d) => setKeys(d.keys || []));
    admin.keysHealth().then(setHealth).catch(() => {});
  }

  useEffect(() => {
    load();
  }, []);

  async function add() {
    try {
      await admin.addKey({
        provider: form.provider,
        key_value: form.key_value,
        label: form.label,
        credits_limit: form.credits_limit ? Number(form.credits_limit) : undefined,
      });
      setMsg("Key added");
      setForm({ ...form, key_value: "" });
      load();
    } catch (e: any) {
      setMsg(e.message);
    }
  }

  return (
    <div className="space-y-6 p-8">
      <header>
        <h1 className="text-2xl font-semibold text-ink">API key pool</h1>
        <p className="text-sm text-ink-muted">
          Serper, Firecrawl, Resend, LinkedIn cookie — never shown after save
        </p>
      </header>

      <div className="grid gap-3 rounded-xl border border-line bg-surface-2/60 p-5 sm:grid-cols-2">
        <select
          className="rounded-lg border border-line bg-surface px-3 py-2 text-sm"
          value={form.provider}
          onChange={(e) => setForm({ ...form, provider: e.target.value })}
        >
          <option value="serper">Serper</option>
          <option value="firecrawl">Firecrawl</option>
          <option value="resend">Resend</option>
          <option value="linkedin_scraper">LinkedIn (li_at)</option>
          <option value="groq">Groq</option>
        </select>
        <input
          placeholder="Label"
          className="rounded-lg border border-line bg-surface px-3 py-2 text-sm"
          value={form.label}
          onChange={(e) => setForm({ ...form, label: e.target.value })}
        />
        <input
          type="password"
          placeholder="Key / cookie value"
          className="rounded-lg border border-line bg-surface px-3 py-2 text-sm sm:col-span-2"
          value={form.key_value}
          onChange={(e) => setForm({ ...form, key_value: e.target.value })}
        />
        <input
          type="number"
          placeholder="Credits limit (optional)"
          className="rounded-lg border border-line bg-surface px-3 py-2 text-sm"
          value={form.credits_limit}
          onChange={(e) => setForm({ ...form, credits_limit: e.target.value })}
        />
        <button aria-label="Action"
          onClick={add}
          disabled={!form.key_value}
          className="btn-primary disabled:opacity-50"
        >
          Add / update key
        </button>
      </div>

      {msg && <p className="text-sm text-brand-700 dark:text-brand-300">{msg}</p>}
      <p className="text-xs text-ink-faint">
        LinkedIn: choose provider <strong>linkedin_scraper</strong> and paste full <code>li_at</code>.
        It becomes active immediately for enrich/scraper (no API restart). Also saved in the key pool for next start.
      </p>

      {health && (
        <pre className="overflow-auto rounded-xl border border-line bg-surface-3/50 p-4 text-xs text-ink-muted">
          {JSON.stringify(health, null, 2)}
        </pre>
      )}

      <div className="overflow-hidden rounded-xl border border-line">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-line bg-surface-2 text-xs uppercase text-ink-faint">
            <tr>
              <th className="px-4 py-3">Provider</th>
              <th className="px-4 py-3">Label</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Usage</th>
              <th className="px-4 py-3">Last error</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {keys.map((k) => (
              <tr key={k.id}>
                <td className="px-4 py-3 text-ink">{k.provider}</td>
                <td className="px-4 py-3 text-ink-muted">{k.label}</td>
                <td className="px-4 py-3">
                  <span
                    className={`rounded px-2 py-0.5 text-xs ${
                      k.status === "active"
                        ? "bg-signal-900/50 text-signal-300"
                        : "bg-rust-900/40 text-rust-300"
                    }`}
                  >
                    {k.status}
                  </span>
                </td>
                <td className="px-4 py-3 tabular-nums text-ink-muted">
                  {k.credits_used || 0}
                  {k.credits_limit != null ? ` / ${k.credits_limit}` : ""}
                </td>
                <td className="max-w-[240px] truncate px-4 py-3 text-xs text-ink-muted">
                  {k.last_error || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
