"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { admin } from "@/lib/admin";

const EDIT_FIELDS = [
  "name", "phone", "email", "website", "address", "city", "state",
  "category", "tier", "owner_name", "pitch_line", "linkedin_url", "instagram_handle",
  "status", "delivery_status",
] as const;

export default function AdminLeadDetailPage() {
  const { tenantId, leadId } = useParams<{ tenantId: string; leadId: string }>();
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  const load = () => {
    if (!leadId) return;
    admin
      .leadDetail(String(leadId), tenantId ? String(tenantId) : undefined)
      .then((d) => {
        setData(d);
        const g = d.lead || {};
        const en = d.enrichment || {};
        const a = d.assignment || {};
        setForm({
          name: g.name || "",
          phone: g.phone_e164 || g.phone_normalized || "",
          email: en.email || "",
          website: g.website || "",
          address: g.address || "",
          city: g.city || "",
          state: g.state || "",
          category: g.category || "",
          tier: (a.tier || d.tier || "B").toString().toUpperCase(),
          owner_name: en.owner_name || "",
          pitch_line: en.pitch_line || "",
          linkedin_url: en.linkedin_url || g.linkedin_url || "",
          instagram_handle: en.instagram_handle || "",
          status: a.status || "new",
          delivery_status: a.delivery_status || "sent",
        });
      })
      .catch((e) => setError(e.message));
  };

  useEffect(() => {
    load();
  }, [leadId, tenantId]);

  async function save() {
    setSaving(true);
    setMsg("");
    try {
      await admin.updateLead(String(leadId), {
        tenant_id: tenantId ? String(tenantId) : undefined,
        ...form,
      });
      setMsg("Saved — client CRM will show the updated fields.");
      setEditing(false);
      load();
    } catch (e: any) {
      setMsg(e?.message || "Save failed");
    } finally {
      setSaving(false);
    }
  }

  if (error) {
    return (
      <div className="space-y-4 p-8">
        <Link href={`/admin/leads/${tenantId}`} className="text-sm text-brand-400">
          ← Back to leads
        </Link>
        <p className="text-rust-400">{error}</p>
      </div>
    );
  }

  if (!data) {
    return <div className="p-8 text-ink-faint">Loading lead…</div>;
  }

  const d = data.display || {};
  const g = data.lead || {};
  const en = data.enrichment || {};
  const sc = data.score || {};
  const a = data.assignment || {};

  const fields: [string, any][] = [
    ["Name", d.name || g.name],
    ["Tier", d.tier || sc.tier || "—"],
    ["Score", d.score ?? sc.total_score ?? "—"],
    ["Phone", d.phone || g.phone_e164 || g.phone_normalized],
    ["Email", d.email || en.email],
    ["Website", d.website || g.website],
    ["Address", d.address || g.address],
    ["City", d.city || g.city],
    ["State", d.state || g.state],
    ["Owner", d.owner || en.owner_name],
    ["Booking", d.booking || en.booking_platform || (en.has_booking ? "Yes" : "—")],
    ["Pitch", d.pitch || en.pitch_line],
    ["LinkedIn", en.linkedin_url || g.linkedin_url],
    ["Instagram", en.instagram_handle],
    ["Rating", d.rating ?? g.google_rating],
    ["Reviews", d.reviews ?? g.review_count],
    ["Source", d.source || g.source],
    ["Category", g.category],
    ["Business status", g.business_status],
    ["Assignment status", a.status || d.status],
    ["Delivery status", a.delivery_status || d.delivery_status],
    ["Score reason", sc.reasoning || sc.score_reason],
    ["Lead ID", g.id || leadId],
  ];

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link href={`/admin/leads/${tenantId}`} className="text-sm text-brand-400 hover:underline">
            ← Back to leads
          </Link>
          <h1 className="mt-2 text-2xl font-semibold text-ink">{d.name || g.name || "Lead"}</h1>
          <p className="text-sm text-ink-faint">
            Tier {(d.tier || "—").toString().toUpperCase()} · Score {d.score ?? "—"}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setEditing((v) => !v)}
            className="rounded-lg border border-line bg-surface-2 px-3 py-1.5 text-sm text-ink hover:border-brand-500"
          >
            {editing ? "Cancel edit" : "Edit lead"}
          </button>
          {(d.website || g.website) && (
            <a
              href={
                String(d.website || g.website).startsWith("http")
                  ? String(d.website || g.website)
                  : `https://${d.website || g.website}`
              }
              target="_blank"
              rel="noreferrer"
              className="rounded-lg border border-line px-3 py-1.5 text-sm text-ink-muted hover:border-brand-500"
            >
              Open website
            </a>
          )}
        </div>
      </div>

      {msg && (
        <p className={`text-sm ${msg.includes("fail") || msg.includes("Fail") ? "text-rust-400" : "text-signal-300"}`}>
          {msg}
        </p>
      )}

      {editing ? (
        <div className="space-y-4 rounded-2xl border border-line bg-surface-2/40 p-6">
          <h2 className="text-sm font-semibold text-ink">Edit lead (updates client CRM)</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {EDIT_FIELDS.map((key) => (
              <label key={key} className="block text-xs">
                <span className="text-ink-faint uppercase tracking-wide">{key.replace(/_/g, " ")}</span>
                {key === "tier" ? (
                  <select
                    className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink"
                    value={form[key] || "B"}
                    onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                  >
                    <option value="A">A</option>
                    <option value="B">B</option>
                    <option value="C">C</option>
                  </select>
                ) : key === "pitch_line" ? (
                  <textarea
                    className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink"
                    rows={2}
                    value={form[key] || ""}
                    onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                  />
                ) : (
                  <input
                    className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink"
                    value={form[key] || ""}
                    onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                  />
                )}
              </label>
            ))}
          </div>
          <button
            type="button"
            disabled={saving}
            onClick={save}
            className="rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save changes"}
          </button>
        </div>
      ) : (
        <div className="overflow-hidden rounded-2xl border border-line bg-surface-2/40">
          <table className="w-full text-left text-sm">
            <tbody>
              {fields.map(([label, value]) => (
                <tr key={label} className="border-b border-line/60 last:border-0">
                  <td className="w-40 px-4 py-2.5 text-xs font-medium uppercase tracking-wide text-ink-faint">
                    {label}
                  </td>
                  <td className="px-4 py-2.5 text-ink break-words">
                    {value === null || value === undefined || value === ""
                      ? "—"
                      : String(value)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
