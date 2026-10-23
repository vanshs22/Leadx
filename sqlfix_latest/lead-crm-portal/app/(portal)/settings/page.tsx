"use client";

import { useEffect, useState } from "react";
import { crm } from "@/lib/api";
import { ErrorState } from "@/components/ui/States";
import { UnavailableNote, FreshnessNote } from "@/components/ui/SurfaceMeta";
import { getTenantId } from "@/lib/tenant";
import { PageHeader } from "@/components/ui/Primitives";
import { getSupabase } from "@/lib/supabaseClient";

export default function SettingsPage() {
  const [brand, setBrand] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [newPassword, setNewPassword] = useState("");
  const [pwMsg, setPwMsg] = useState("");
  const [pwBusy, setPwBusy] = useState(false);
  const [settingsLoading, setSettingsLoading] = useState(true);
  const [settingsError, setSettingsError] = useState<unknown>(null);

  async function changePassword() {
    if (newPassword.length < 8) {
      setPwMsg("Password must be at least 8 characters");
      return;
    }
    setPwBusy(true);
    setPwMsg("");
    try {
      const { error } = await getSupabase().auth.updateUser({ password: newPassword });
      if (error) throw new Error(error.message);
      setPwMsg("Password updated");
      setNewPassword("");
    } catch (e: any) {
      setPwMsg(e.message || "Failed to update password");
    } finally {
      setPwBusy(false);
    }
  }


  async function loadBranding() {
    setSettingsLoading(true);
    setSettingsError(null);
    try {
      const b = await crm.branding(getTenantId());
      setBrand({
        brand_name: b.brand_name || "",
        brand_primary_color: b.brand_primary_color || "",
        brand_logo_url: b.brand_logo_url || "",
        support_email: b.support_email || "",
      });
    } catch (e: unknown) {
      setSettingsError(e);
    } finally {
      setSettingsLoading(false);
    }
  }

  useEffect(() => { void loadBranding(); }, []);

  async function save() {
    setBusy(true);
    try {
      await crm.updateBranding(getTenantId(), brand);
      setMsg("Branding saved");
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function seedStages() {
    setBusy(true);
    try {
      await crm.setup(getTenantId());
      setMsg("Pipeline stages seeded");
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (settingsLoading) return <div className="page text-ink-faint" aria-busy="true">Loading settings…</div>;
  if (settingsError) return <div className="page"><ErrorState error={settingsError} onRetry={loadBranding} /></div>;

  return (
    <div className="page max-w-xl">
      <PageHeader eyebrow="Workspace settings" title="Settings" description="Workspace branding, pipeline setup, and account access. Changes apply to this workspace only." />

      {msg && (
        <div className="rounded-xl border border-line bg-surface-2 px-4 py-2.5 text-sm text-ink-muted">{msg}</div>
      )}

      <section className="card-pad space-y-3">
        <h2 className="text-sm font-semibold text-ink">Branding</h2>
        {(["brand_name", "brand_primary_color", "brand_logo_url", "support_email"] as const).map((k) => (
          <label key={k} className="block text-xs text-ink-faint">
            {k.replace(/_/g, " ")}
            <input
              className="input mt-1"
              value={brand[k] || ""}
              onChange={(e) => setBrand({ ...brand, [k]: e.target.value })}
            />
          </label>
        ))}
        <button type="button" aria-label="Save workspace branding" onClick={save} disabled={busy} className="btn-primary">
          Save branding
        </button>
      </section>

      <section className="card-pad space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-sm font-semibold text-ink">Scoring preferences</h2><FreshnessNote label="Configuration status" detail="read-only here" /></div>
        <p className="text-sm text-ink-muted">Lead ranking weights are managed per ICP by an operator. This workspace view does not expose an ICP selector or a read endpoint, so LeadX will not imply that these values are editable here.</p>
        <UnavailableNote label="ICP scoring editor">Ask your operator to update weights for a specific ICP.</UnavailableNote>
      </section>

      <section className="card-pad space-y-3">
        <h2 className="text-sm font-semibold text-ink">Account</h2>
        <p className="text-sm text-ink-muted">Change the password your admin set when your account was created.</p>
        <div className="flex gap-2">
          <input
            type="password"
            placeholder="New password"
            className="input flex-1"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
          />
          <button type="button" aria-label="Update account password" onClick={changePassword} disabled={pwBusy} className="btn-secondary shrink-0">
            Update
          </button>
        </div>
        {pwMsg && <p className="text-xs text-ink-muted">{pwMsg}</p>}
      </section>

      <section className="card-pad space-y-3">
        <h2 className="text-sm font-semibold text-ink">Pipeline</h2>
        <p className="text-sm text-ink-muted">Seed default stages (New → Contacted → Qualified → Won / Lost).</p>
        <button type="button" aria-label="Seed default pipeline stages" onClick={seedStages} disabled={busy} className="btn-secondary">
          Seed stages
        </button>
      </section>
    </div>
  );
}
