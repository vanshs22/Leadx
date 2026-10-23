/**
 * Active workspace (tenant) — stored in localStorage, not NEXT_PUBLIC_*.
 */
const KEY = "lead_crm_tenant_id";
const NAME_KEY = "lead_crm_tenant_name";
const USER_KEY = "lead_crm_tenant_user_id";

export function getTenantId(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(KEY) || "";
}

export function getTenantUserId(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(USER_KEY) || "";
}

export function getTenantName(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(NAME_KEY) || "";
}

export function setTenant(id: string, name?: string, userId?: string) {
  if (typeof window === "undefined") return;
  localStorage.setItem(KEY, id);
  if (name) localStorage.setItem(NAME_KEY, name);
  if (userId) localStorage.setItem(USER_KEY, userId);
}

export function clearTenant() {
  if (typeof window === "undefined") return;
  localStorage.removeItem(KEY);
  localStorage.removeItem(NAME_KEY);
  localStorage.removeItem(USER_KEY);
}

/** Hook-friendly: read tenant; empty means user must pick workspace */
export function useTenantId(): string {
  if (typeof window === "undefined") return "";
  return getTenantId();
}
