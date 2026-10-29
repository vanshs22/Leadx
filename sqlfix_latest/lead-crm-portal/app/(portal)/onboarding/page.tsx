"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { crm } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { Notice, PageHeader, Panel } from "@/components/ui/Primitives";
import { EmptyState } from "@/components/ui/States";
import { Icon } from "@/components/ui/Icon";

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
    <div className="page max-w-3xl">
      <PageHeader eyebrow="Onboarding" title="Get started" description="Four quick steps — then you can find and email leads." meta={<span className="tabular-nums">Step {step + 1} of {STEPS.length}</span>} />

      {/* Step indicator */}
      <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label="Onboarding progress">
        {STEPS.map((label, i) => {
          const done = i < step, active = i === step;
          return (
            <li key={label} aria-current={active ? "step" : undefined} className="flex min-w-0 items-center gap-2.5">
              <span
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold tabular-nums ${
                  done
                    ? "bg-emerald-600 text-white dark:bg-emerald-400 dark:text-emerald-950"
                    : active
                      ? "bg-brand-700 text-white ring-4 ring-brand-500/15 dark:bg-brand-300 dark:text-brand-900"
                      : "border border-line-strong bg-surface-2 text-ink-faint"
                }`}
                aria-hidden="true"
              >
                {done ? <Icon name="check" size={14} /> : i + 1}
              </span>
              <span className={`truncate text-[0.8125rem] font-medium ${active ? "text-ink" : done ? "text-ink-muted" : "text-ink-faint"}`}>
                {label}<span className="sr-only">{done ? " (complete)" : active ? " (current step)" : ""}</span>
              </span>
            </li>
          );
        })}
      </ol>
      <div className="h-1 overflow-hidden rounded-full bg-surface-3" aria-hidden="true"><div className="h-full rounded-full bg-brand-600 transition-[width] dark:bg-brand-300" style={{ width: `${((step + 1) / STEPS.length) * 100}%` }} /></div>

      {msg && <Notice tone="danger">{msg}</Notice>}

      {/* Step 0 — ICP */}
      {step === 0 && (
        <Panel
          title="Who do you want to find?"
          description="This becomes your first ideal customer profile."
          footer={<><span>You can refine this later.</span><button disabled={busy || !vertical.trim()} className="btn-primary" onClick={saveIcpAndContinue}>
            {busy ? "Saving…" : "Continue"}
          </button></>}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor="onb-vertical" className="field-label">Vertical</label>
              <input id="onb-vertical" className="input" value={vertical} onChange={(e) => setVertical(e.target.value)} />
            </div>
            <div>
              <label htmlFor="onb-offer" className="field-label">Your offer category</label>
              <input id="onb-offer" className="input" value={offer} onChange={(e) => setOffer(e.target.value)} />
            </div>
            <div>
              <label htmlFor="onb-geo" className="field-label">Geo</label>
              <input id="onb-geo" className="input" value={geo} onChange={(e) => setGeo(e.target.value)} placeholder="Hoboken, Jersey City" />
              <p className="field-hint">City or region, comma-separated.</p>
            </div>
            <div>
              <label htmlFor="onb-keywords" className="field-label">Keywords</label>
              <input id="onb-keywords" className="input" value={keywords} onChange={(e) => setKeywords(e.target.value)} placeholder="botox, facial, aesthetics" />
              <p className="field-hint">Comma-separated.</p>
            </div>
          </div>
        </Panel>
      )}

      {/* Step 1 — Delivery */}
      {step === 1 && (
        <Panel
          title="How should we deliver leads?"
          description="Choose where new qualified leads show up."
          footer={<><button className="btn-ghost" onClick={() => setStep(0)}>Back</button><button disabled={busy} className="btn-primary" onClick={saveDeliveryAndContinue}>
            {busy ? "Saving…" : "Continue"}
          </button></>}
        >
          <div className="space-y-4">
            <div className="max-w-sm">
              <label htmlFor="onb-channel" className="field-label">Delivery channel</label>
              <select id="onb-channel" className="select" value={channel} onChange={(e) => setChannel(e.target.value)}>
                <option value="portal">CRM portal only</option>
                <option value="telegram">Telegram</option>
                <option value="webhook">Webhook</option>
                <option value="email">Email digest</option>
              </select>
            </div>
            {channel === "telegram" && (
              <div className="max-w-sm">
                <label htmlFor="onb-telegram" className="field-label">Telegram chat ID</label>
                <input id="onb-telegram" className="input font-mono" value={telegramChat} onChange={(e) => setTelegramChat(e.target.value)} />
              </div>
            )}
            {channel === "webhook" && (
              <div>
                <label htmlFor="onb-webhook" className="field-label">Webhook URL</label>
                <input id="onb-webhook" className="input font-mono" value={webhookUrl} onChange={(e) => setWebhookUrl(e.target.value)} />
                <p className="field-hint">We POST each delivered lead as JSON.</p>
              </div>
            )}
          </div>
        </Panel>
      )}

      {/* Step 2 — Sample preview */}
      {step === 2 && (
        <Panel
          title="Preview sample leads"
          description="See lead quality before you go live."
          footer={<><button className="btn-ghost" onClick={() => setStep(1)}>Back</button><button disabled={busy} className="btn-primary" onClick={loadSampleAndContinue}>
            {busy ? "Loading sample…" : "Load sample pack"}
          </button></>}
        >
          <p className="text-[0.8125rem] leading-6 text-ink-muted">
            We&apos;ll pull a free sample pack for <strong className="font-semibold text-ink">{vertical}</strong>
            {geo ? ` in ${geo}` : ""} so you can see quality before going live.
          </p>
        </Panel>
      )}

      {/* Step 3 — Go live */}
      {step === 3 && (
        <Panel
          title="You're ready"
          description="Head to Search to run your first real find & email campaign."
          bodyClassName={sampleLeads.length > 0 ? "p-0" : "panel-body"}
          footer={<><span>Sample pack loaded: <strong className="tabular-nums text-ink">{sampleCount}</strong> leads.</span><button className="btn-primary" onClick={goLive}>
            Go to Search
          </button></>}
        >
          {sampleLeads.length > 0 ? (
            <div className="max-h-72 overflow-auto">
              <table className="data-table !min-w-[420px]">
                <caption className="sr-only">Sample leads</caption>
                <thead><tr><th scope="col">Business</th><th scope="col">City</th><th scope="col" className="!text-right">Score</th></tr></thead>
                <tbody>
                  {sampleLeads.slice(0, 8).map((l: any, i: number) => (
                    <tr key={i}>
                      <td className="font-medium text-ink">{l.name}</td>
                      <td className="text-ink-muted">{l.city}</td>
                      <td className="text-right tabular-nums">{l.score ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <EmptyState title="No sample leads returned" hint="You can still go live and run a search." />}
        </Panel>
      )}
    </div>
  );
}
