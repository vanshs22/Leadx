"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { crm } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";

const STEPS = ["ICP setup", "Delivery", "Sample preview", "Go live"];

export default function OnboardingWizardPage() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  // Step 1 — ICP
  const [vertical, setVertical] = useState("medspa");
  const [geo, setGeo] = useState("");
  const [keywords, setKeywords] = useState("");
  const [offer, setOffer] = useState("lead_gen_service");

  // Step 2 — Delivery
  const [channel, setChannel] = useState("portal");
  const [webhookUrl, setWebhookUrl] = useState("");
  const [telegramChat, setTelegramChat] = useState("");

  // Step 3 — Sample
  const [sampleLeads, setSampleLeads] = useState<any[]>([]);
  const [sampleCount, setSampleCount] = useState(0);

  async function saveIcpAndContinue() {
    setBusy(true);
    setMsg("");
    try {
      const tid = getTenantId();
      if (!tid) throw new Error("No workspace selected");
      // Seed CRM stages if needed
      await crm.setup(tid, {}).catch(() => {});
      // A light search-run with save_icp creates the ICP profile
      await crm.searchRun(tid, {
        vertical: vertical.trim(),
        geo: geo.split(",").map((s) => s.trim()).filter(Boolean),
        keywords: keywords.split(",").map((s) => s.trim()).filter(Boolean),
        offer_category: offer.trim() || "lead_gen_service",
        limit_per_location: 5,
        allow_tier_b: true,
        save_icp: true,
        name: `${vertical} — ${geo || "default"}`,
      });
      setStep(1);
    } catch (e: any) {
      setMsg(e.message || "Could not save ICP");
    } finally {
      setBusy(false);
    }
  }

  async function saveDeliveryAndContinue() {
    setBusy(true);
    setMsg("");
    try {
      const tid = getTenantId();
      if (!tid) throw new Error("No workspace selected");
      // Store delivery prefs via branding/settings patch when available
      await crm.updateBranding(tid, {
        delivery_channel: channel,
        webhook_url: webhookUrl || undefined,
        telegram_chat_id: telegramChat || undefined,
      }).catch(() => {
        // Non-fatal if branding endpoint rejects unknown fields
      });
      setStep(2);
    } catch (e: any) {
      setMsg(e.message || "Could not save delivery");
    } finally {
      setBusy(false);
    }
  }

  async function loadSampleAndContinue() {
    setBusy(true);
    setMsg("");
    try {
      const tid = getTenantId();
      if (!tid) throw new Error("No workspace selected");
      const r = await crm.samplePack(tid, {
        vertical: vertical.trim(),
        geo: geo.trim(),
        limit: 10,
        min_score: 60,
      });
      setSampleLeads(r.leads || []);
      setSampleCount(r.count || 0);
      setStep(3);
    } catch (e: any) {
      setMsg(e.message || "Could not load sample pack");
    } finally {
      setBusy(false);
    }
  }

  function goLive() {
    router.push("/search");
  }

  return (
    <div className="page max-w-2xl mx-auto">
      <header>
        <h1 className="page-title">Get started</h1>
        <p className="page-sub">Four quick steps — then you can find and email leads.</p>
      </header>

      {/* Step indicator */}
      <div className="flex gap-2 mb-6">
        {STEPS.map((label, i) => (
          <div
            key={label}
            className={`flex-1 rounded-lg px-2 py-2 text-center text-xs font-medium ${
              i === step
                ? "bg-brand-600 text-white"
                : i < step
                  ? "bg-brand-50 text-brand-700 dark:bg-brand-500/20 dark:text-brand-300"
                  : "bg-surface-2 text-ink-faint"
            }`}
          >
            {i + 1}. {label}
          </div>
        ))}
      </div>

      {msg && (
        <div className="mb-4 rounded-xl border border-line bg-surface-2 px-4 py-2.5 text-sm text-ink-muted">
          {msg}
        </div>
      )}

      {/* Step 0 — ICP */}
      {step === 0 && (
        <div className="card-pad space-y-4">
          <h2 className="text-sm font-semibold">Who do you want to find?</h2>
          <label className="block text-xs font-medium text-ink-faint">
            Vertical
            <input className="input mt-1" value={vertical} onChange={(e) => setVertical(e.target.value)} />
          </label>
          <label className="block text-xs font-medium text-ink-faint">
            Geo (city or region, comma-separated)
            <input className="input mt-1" value={geo} onChange={(e) => setGeo(e.target.value)} placeholder="Hoboken, Jersey City" />
          </label>
          <label className="block text-xs font-medium text-ink-faint">
            Keywords
            <input className="input mt-1" value={keywords} onChange={(e) => setKeywords(e.target.value)} placeholder="botox, facial, aesthetics" />
          </label>
          <label className="block text-xs font-medium text-ink-faint">
            Your offer category
            <input className="input mt-1" value={offer} onChange={(e) => setOffer(e.target.value)} />
          </label>
          <button disabled={busy || !vertical.trim()} className="btn-primary w-full" onClick={saveIcpAndContinue}>
            {busy ? "Saving…" : "Continue"}
          </button>
        </div>
      )}

      {/* Step 1 — Delivery */}
      {step === 1 && (
        <div className="card-pad space-y-4">
          <h2 className="text-sm font-semibold">How should we deliver leads?</h2>
          <select className="input" value={channel} onChange={(e) => setChannel(e.target.value)}>
            <option value="portal">CRM portal only</option>
            <option value="telegram">Telegram</option>
            <option value="webhook">Webhook</option>
            <option value="email">Email digest</option>
          </select>
          {channel === "telegram" && (
            <label className="block text-xs font-medium text-ink-faint">
              Telegram chat ID
              <input className="input mt-1" value={telegramChat} onChange={(e) => setTelegramChat(e.target.value)} />
            </label>
          )}
          {channel === "webhook" && (
            <label className="block text-xs font-medium text-ink-faint">
              Webhook URL
              <input className="input mt-1" value={webhookUrl} onChange={(e) => setWebhookUrl(e.target.value)} />
            </label>
          )}
          <div className="flex gap-2">
            <button className="btn-secondary" onClick={() => setStep(0)}>Back</button>
            <button disabled={busy} className="btn-primary flex-1" onClick={saveDeliveryAndContinue}>
              {busy ? "Saving…" : "Continue"}
            </button>
          </div>
        </div>
      )}

      {/* Step 2 — Sample preview */}
      {step === 2 && (
        <div className="card-pad space-y-4">
          <h2 className="text-sm font-semibold">Preview sample leads</h2>
          <p className="text-sm text-ink-muted">
            We&apos;ll pull a free sample pack for <strong>{vertical}</strong>
            {geo ? ` in ${geo}` : ""} so you can see quality before going live.
          </p>
          <div className="flex gap-2">
            <button className="btn-secondary" onClick={() => setStep(1)}>Back</button>
            <button disabled={busy} className="btn-primary flex-1" onClick={loadSampleAndContinue}>
              {busy ? "Loading sample…" : "Load sample pack"}
            </button>
          </div>
        </div>
      )}

      {/* Step 3 — Go live */}
      {step === 3 && (
        <div className="card-pad space-y-4">
          <h2 className="text-sm font-semibold">You&apos;re ready</h2>
          <p className="text-sm text-ink-muted">
            Sample pack loaded: <strong>{sampleCount}</strong> leads.
            Head to Search to run your first real find & email campaign.
          </p>
          {sampleLeads.length > 0 && (
            <ul className="space-y-2 max-h-60 overflow-y-auto">
              {sampleLeads.slice(0, 8).map((l: any, i: number) => (
                <li key={i} className="rounded-lg border border-line px-3 py-2 text-sm">
                  <span className="font-medium text-ink">{l.name}</span>
                  <span className="text-ink-faint"> · {l.city} · score {l.score ?? "—"}</span>
                </li>
              ))}
            </ul>
          )}
          <button className="btn-primary w-full" onClick={goLive}>
            Go to Search
          </button>
        </div>
      )}
    </div>
  );
}
