"use client";

import { useEffect, useState } from "react";
import { getTenantId } from "@/lib/tenant";
import { useCrmRealtime, useCrmChange, type CrmRealtimeState } from "@/lib/crmRealtime";

const LABEL: Record<CrmRealtimeState, string> = {
  connecting: "Connecting", connected: "Live", reconnecting: "Reconnecting", offline: "Offline"
};

export function RealtimeStatus() {
  const [tenantReady, setTenantReady] = useState(false);
  useEffect(() => {
    const check = () => setTenantReady(Boolean(getTenantId()));
    check();
    window.addEventListener("leadx:tenant-ready", check);
    window.addEventListener("storage", check);
    return () => { window.removeEventListener("leadx:tenant-ready", check); window.removeEventListener("storage", check); };
  }, []);
  const state = useCrmRealtime(undefined, tenantReady ? getTenantId() : "");
  const [lastSync, setLastSync] = useState<number | null>(null);
  useCrmChange(() => setLastSync(Date.now()));
  const dot = state === "connected" ? "bg-signal-500" : state === "offline" ? "bg-rust-500" : "bg-brand-500";
  const title = lastSync ? `CRM realtime: ${LABEL[state]} · last change ${new Date(lastSync).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : `CRM realtime synchronization: ${LABEL[state]}`;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface-2/80 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-ink-faint shadow-sm" title={title}>
      {state === "connected"
        ? <span className="live-dot" style={{ width: 6, height: 6 }} />
        : <span className={`h-1.5 w-1.5 rounded-full ${dot} ${state === "reconnecting" || state === "connecting" ? "animate-pulse" : ""}`} />}
      {LABEL[state]}
    </span>
  );
}
