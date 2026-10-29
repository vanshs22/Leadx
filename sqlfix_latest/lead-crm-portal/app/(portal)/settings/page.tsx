"use client";

import { useEffect, useState } from "react";
import { crm } from "@/lib/api";
import Link from "next/link";
import { ErrorState, LoadingRows } from "@/components/ui/States";
import { UnavailableNote, FreshnessNote } from "@/components/ui/SurfaceMeta";
import { getTenantId } from "@/lib/tenant";
import { Badge, Notice, PageHeader, Panel } from "@/components/ui/Primitives";
import { getSupabase } from "@/lib/supabaseClient";

/** Local settings sub-navigation (kept in-file so no shared primitive is added). */
function SettingsNav() {
  const items = [
    { href: "/settings/email", label: "Email & domain" },
    { href: "/settings/integrations", label: "Integrations" },
    { href: "/settings/billing", label: "Billing" },
    { href: "/team", label: "Team" },
  ];
  return <nav aria-label="Settings sections" className="flex flex-wrap gap-1.5">{items.map((item) => <Link key={item.href} href={item.href} className="btn-ghost btn-sm">{item.label}</Link>)}</nav>;
}

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

  const header = <PageHeader eyebrow="Workspace settings" title="Settings" description="Workspace branding, pipeline setup, and account access. Changes apply to this workspace only." actions={<SettingsNav />} />;

  if (settingsLoading) return <div className="page max-w-4xl">{header}<div className="card" aria-busy="true"><span className="sr-only">Loading settings…</span><LoadingRows rows={4} cols={2} /></div></div>;
  if (settingsError) return <div className="page max-w-4xl">{header}<ErrorState error={settingsError} onRetry={loadBranding} /></div>;

  const fieldMeta: Record<string, { label: string; hint: string; placeholder: string; inputMode?: "url" | "email" | "text" }> = {
    brand_name: { label: "Brand name", hint: "Shown in the workspace header and outbound templates.", placeholder: "Acme Growth" },
    brand_primary_color: { label: "Primary colour", hint: "Hex value used for client-facing assets.", placeholder: "Hex colour value" },
    brand_logo_url: { label: "Logo URL", hint: "HTTPS link to a square PNG or SVG.", placeholder: "https://…", inputMode: "url" },
    support_email: { label: "Support email", hint: "Where client replies and questions are routed.", placeholder: "support@company.com", inputMode: "email" },
  };

  return (
    <div className="page max-w-4xl">
      {header}

      {msg && <Notice tone={/saved|seeded/i.test(msg) ? "success" : "neutral"}>{msg}</Notice>}

      <Panel
        id="settings-branding"
        title="Branding"
        description="How this workspace presents itself to your team and clients."
        footer={<><span>Applies to this workspace only.</span><button type="button" aria-label="Save workspace branding" onClick={save} disabled={busy} className="btn-primary">Save branding</button></>}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          {(["brand_name", "brand_primary_color", "brand_logo_url", "support_email"] as const).map((k) => (
            <div key={k}>
              <label htmlFor={`settings-${k}`} className="field-label">{fieldMeta[k].label}</label>
              <input
                id={`settings-${k}`}
                className="input"
                inputMode={fieldMeta[k].inputMode || "text"}
                placeholder={fieldMeta[k].placeholder}
                value={brand[k] || ""}
                onChange={(e) => setBrand({ ...brand, [k]: e.target.value })}
              />
              <p className="field-hint">{fieldMeta[k].hint}</p>
            </div>
          ))}
        </div>
      </Panel>

      <Panel
        id="settings-account"
        title="Account"
        description="Change the password your admin set when your account was created."
        footer={<><span aria-live="polite">{pwMsg || "Minimum 8 characters."}</span><button type="button" aria-label="Update account password" onClick={changePassword} disabled={pwBusy} className="btn-primary">{pwBusy ? "Updating…" : "Update password"}</button></>}
      >
        <div className="max-w-sm">
          <label htmlFor="settings-new-password" className="field-label">New password</label>
          <input
            id="settings-new-password"
            type="password"
            autoComplete="new-password"
            placeholder="New password"
            className="input"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
          />
        </div>
      </Panel>

      <div className="grid gap-4 md:grid-cols-2">
        <Panel
          id="settings-pipeline"
          title="Pipeline"
          description="Seed default stages (New → Contacted → Qualified → Won / Lost)."
          footer={<><span>Safe to run again.</span><button type="button" aria-label="Seed default pipeline stages" onClick={seedStages} disabled={busy} className="btn-secondary">Seed stages</button></>}
        >
          <ol className="flex flex-wrap items-center gap-1.5 text-xs">
            {["New", "Contacted", "Qualified", "Won", "Lost"].map((s) => <li key={s}><Badge tone={s === "Won" ? "success" : s === "Lost" ? "danger" : "neutral"}>{s}</Badge></li>)}
          </ol>
        </Panel>

        <Panel id="settings-scoring" title="Scoring preferences" actions={<FreshnessNote label="Configuration status" detail="read-only here" />}>
          <div className="space-y-3">
            <p className="text-[0.8125rem] leading-6 text-ink-muted">Lead ranking weights are managed per ICP by an operator. This workspace view does not expose an ICP selector or a read endpoint, so LeadX will not imply that these values are editable here.</p>
            <UnavailableNote label="ICP scoring editor">Ask your operator to update weights for a specific ICP.</UnavailableNote>
          </div>
        </Panel>
      </div>
    </div>
  );
}
