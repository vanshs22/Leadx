/**
 * Saved views, localStorage edition.
 *
 * Note: `components/leads/SavedViews.tsx` already does this server-side via
 * crmExtras.views (shared across the team, supports a default view). Prefer
 * that one. This module exists for pages that want a zero-backend, per-browser
 * version — currently the deals board.
 *
 * Keyed per tenant so switching workspace doesn't leak filters across them.
 */
import { getTenantId } from "./tenant";

export interface LocalView<S = Record<string, unknown>> {
  name: string;
  state: S;
}

function storageKey(pageKey: string): string {
  return `lead_crm_views:${getTenantId() || "anon"}:${pageKey}`;
}

export function listViews<S = Record<string, unknown>>(pageKey: string): LocalView<S>[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(storageKey(pageKey));
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as LocalView<S>[]) : [];
  } catch {
    // Corrupt or non-JSON value: behave as if nothing was saved.
    return [];
  }
}

/** Upsert by name — saving twice under one name overwrites, not duplicates. */
export function saveView<S = Record<string, unknown>>(
  pageKey: string,
  name: string,
  state: S
): LocalView<S>[] {
  const clean = name.trim();
  if (typeof window === "undefined" || !clean) return listViews<S>(pageKey);
  const next = [...listViews<S>(pageKey).filter((v) => v.name !== clean), { name: clean, state }];
  localStorage.setItem(storageKey(pageKey), JSON.stringify(next));
  return next;
}

export function deleteView<S = Record<string, unknown>>(
  pageKey: string,
  name: string
): LocalView<S>[] {
  if (typeof window === "undefined") return [];
  const next = listViews<S>(pageKey).filter((v) => v.name !== name);
  localStorage.setItem(storageKey(pageKey), JSON.stringify(next));
  return next;
}
