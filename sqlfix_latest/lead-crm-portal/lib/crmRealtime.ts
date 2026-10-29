"use client";

/** LeadX 8.0: one private Broadcast channel per tenant/browser. */
import { useEffect, useRef, useState } from "react";
import { getSupabase } from "./supabaseClient";
import { isLocalAuthProfile } from "./config";
import { getTenantId } from "./tenant";
import { invalidateApiCache } from "./api";

export type CrmRealtimeState = "connecting" | "connected" | "reconnecting" | "offline";
export const CRM_REALTIME_EVENT = "leadx:crm-change";
export type CrmChangeDetail = { table?: string; event?: string; tenantId?: string };

export function emitCrmChange(detail?: CrmChangeDetail, tenantId?: string) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(CRM_REALTIME_EVENT, { detail: { ...(detail || {}), ...(tenantId ? { tenantId } : {}) } }));
}

type Manager = {
  tenantId: string;
  channel: any | null;
  state: CrmRealtimeState;
  retry: number;
  reconnectTimer?: number;
  refreshTimer?: number;
  disposed: boolean;
  listeners: Set<() => void>;
  stateListeners: Set<(state: CrmRealtimeState) => void>;
  connecting: boolean;
};
const managers = new Map<string, Manager>();

function publishState(manager: Manager, state: CrmRealtimeState) {
  if (manager.disposed) return;
  manager.state = state;
  manager.stateListeners.forEach((listener) => listener(state));
}

function scheduleRefresh(manager: Manager, detail?: CrmChangeDetail) {
  if (typeof window === "undefined" || manager.disposed) return;
  if (manager.refreshTimer) window.clearTimeout(manager.refreshTimer);
  manager.refreshTimer = window.setTimeout(() => {
    manager.refreshTimer = undefined;
    // A push event invalidates only this tenant's short-lived GET snapshot.
    // Mounted screens then fetch their authoritative data; inflight GET
    // de-duplication means several widgets still use one request.
    invalidateApiCache(`/crm/${encodeURIComponent(manager.tenantId)}/`, manager.tenantId);
    emitCrmChange(detail, manager.tenantId);
    manager.listeners.forEach((listener) => listener());
  }, 100);
}

async function removeChannel(manager: Manager) {
  const channel = manager.channel;
  manager.channel = null;
  if (!channel) return;
  try { await getSupabase().removeChannel(channel); } catch {}
}

async function connect(manager: Manager) {
  if (manager.disposed || manager.connecting) return;
  manager.connecting = true;
  publishState(manager, manager.retry ? "reconnecting" : "connecting");
  if (isLocalAuthProfile()) {
    manager.connecting = false;
    publishState(manager, "offline");
    return;
  }
  await removeChannel(manager);
  if (manager.disposed) { manager.connecting = false; return; }

  const sb = getSupabase();
  try { await sb.realtime.setAuth(); } catch {}
  const channel = sb.channel(`crm:${manager.tenantId}`, {
    // Private Broadcast is authorized by the browser Supabase session. The API
    // session remains authoritative for business data; this channel carries
    // tenant-scoped invalidation-only events and never projection payloads.
    config: { private: true, broadcast: { self: false, ack: false } },
  });
  manager.channel = channel;

  channel.on("broadcast", { event: "crm_change" }, (payload: any) => {
    const detail = payload?.payload || {};
    scheduleRefresh(manager, {
      table: typeof detail.table === "string" ? detail.table : undefined,
      event: typeof detail.event === "string" ? detail.event : undefined,
    });
  });

  channel.subscribe((status: string) => {
    if (manager.disposed || manager.channel !== channel) return;
    if (status === "SUBSCRIBED") {
      const recovered = manager.retry > 0 || manager.state === "reconnecting";
      manager.connecting = false;
      manager.retry = 0;
      publishState(manager, "connected");
      // Broadcast is an invalidation signal. A reconnect may have missed any
      // number of changes, so force one authoritative reconciliation after
      // the channel is live again.
      if (recovered) scheduleRefresh(manager, { event: "reconcile" });
      return;
    }
    if (status === "CHANNEL_ERROR" || status === "TIMED_OUT" || status === "CLOSED") {
      manager.connecting = false;
      publishState(manager, "reconnecting");
      const delay = Math.min(30000, 1000 * 2 ** Math.min(manager.retry, 5));
      manager.retry += 1;
      if (manager.reconnectTimer) window.clearTimeout(manager.reconnectTimer);
      manager.reconnectTimer = window.setTimeout(() => {
        manager.reconnectTimer = undefined;
        void connect(manager);
      }, delay);
    }
  });
}

function acquireManager(tenantId: string, onChange?: () => void, onState?: (state: CrmRealtimeState) => void) {
  let manager = managers.get(tenantId);
  if (!manager) {
    manager = { tenantId, channel: null, state: "connecting", retry: 0, disposed: false, listeners: new Set(), stateListeners: new Set(), connecting: false };
    managers.set(tenantId, manager);
    void connect(manager);
  }
  if (onChange) manager.listeners.add(onChange);
  if (onState) { manager.stateListeners.add(onState); onState(manager.state); }
}

export function disposeTenant(tenantId: string) {
  const manager = managers.get(tenantId);
  if (!manager) return;
  manager.disposed = true;
  if (manager.reconnectTimer) window.clearTimeout(manager.reconnectTimer);
  if (manager.refreshTimer) window.clearTimeout(manager.refreshTimer);
  manager.listeners.clear();
  manager.stateListeners.clear();
  managers.delete(tenantId);
  void removeChannel(manager);
}

function releaseManager(tenantId: string, onChange?: () => void, onState?: (state: CrmRealtimeState) => void) {
  const manager = managers.get(tenantId);
  if (!manager) return;
  if (onChange) manager.listeners.delete(onChange);
  if (onState) manager.stateListeners.delete(onState);
  if (manager.listeners.size || manager.stateListeners.size) return;
  manager.disposed = true;
  if (manager.reconnectTimer) window.clearTimeout(manager.reconnectTimer);
  if (manager.refreshTimer) window.clearTimeout(manager.refreshTimer);
  managers.delete(tenantId);
  void removeChannel(manager);
}

export function useCrmRealtime(onChange?: (() => void), tenantIdOverride?: string): CrmRealtimeState {
  const tenantId = tenantIdOverride || getTenantId();
  const [state, setState] = useState<CrmRealtimeState>(tenantId ? "connecting" : "offline");
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    if (!tenantId) { setState("offline"); return; }
    const handleChange = () => onChangeRef.current?.();
    const handleState = (next: CrmRealtimeState) => setState(next);
    acquireManager(tenantId, handleChange, handleState);
    const onVisibility = () => {
      if (document.visibilityState !== "visible") return;
      const manager = managers.get(tenantId);
      if (!manager) return;
      // Reconcile even when the socket still reports connected: a hidden tab
      // can miss Broadcast delivery while suspended by the browser.
      scheduleRefresh(manager, { event: "reconcile" });
      if (manager.state !== "connected" && !manager.connecting) void connect(manager);
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      releaseManager(tenantId, handleChange, handleState);
    };
  }, [tenantId]);
  return state;
}

export function useCrmChange(onChange: (detail?: CrmChangeDetail) => void, options?: { tables?: string[]; tenantId?: string }) {
  const cb = useRef(onChange);
  const tablesRef = useRef(options?.tables);
  const tenantRef = useRef(options?.tenantId);
  cb.current = onChange;
  tablesRef.current = options?.tables;
  tenantRef.current = options?.tenantId;
  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent).detail as CrmChangeDetail | undefined;
      const currentTenant = tenantRef.current || getTenantId();
      // Browser events are hints only. A tenant marker is used solely to keep
      // one mounted workspace from reacting to another workspace's channel.
      if (detail?.tenantId && currentTenant && detail.tenantId !== currentTenant) return;
      const allowed = tablesRef.current;
      if (allowed?.length && detail?.event === "reconcile") {
        cb.current(detail);
        return;
      }
      if (allowed?.length && detail?.table && !allowed.includes(detail.table)) return;
      if (allowed?.length && !detail?.table) return;
      cb.current(detail);
    };
    window.addEventListener(CRM_REALTIME_EVENT, handler);
    return () => window.removeEventListener(CRM_REALTIME_EVENT, handler);
  }, []);
}
