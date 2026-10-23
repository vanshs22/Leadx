"use client";

/**
 * Saved views — name a filter combination once, reuse it forever.
 *
 * Filters are opaque JSON on the backend, so this component owns their
 * shape: whatever object the page hands in comes back out unchanged.
 * Adding a new filter control needs no backend change.
 */
import { useCallback, useEffect, useState } from "react";

import { crmExtras, type SavedView } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";

export function SavedViews<T extends Record<string, any>>({
  entity = "leads",
  current,
  onApply,
}: {
  entity?: string;
  /** The page's live filter state — what "Save current" persists. */
  current: T;
  onApply: (filters: T) => void;
}) {
  const [views, setViews] = useState<SavedView[]>([]);
  const [activeId, setActiveId] = useState("");
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const appliedDefault = useState({ done: false })[0];

  const load = useCallback(async () => {
    try {
      const d = await crmExtras.views(getTenantId(), entity);
      setViews(d.views || []);
      // Apply the default view once on first load — the point of marking
      // one default is not having to click it every visit.
      if (!appliedDefault.done) {
        appliedDefault.done = true;
        const def = (d.views || []).find((v) => v.is_default);
        if (def) {
          setActiveId(def.id);
          onApply(def.filters as T);
        }
      }
    } catch (e: any) {
      setError(e.message);
    }
    // onApply is a fresh closure each render; re-running on it would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entity]);

  useEffect(() => {
    load();
  }, [load]);

  async function save() {
    if (!name.trim()) return;
    setError("");
    const r = await crmExtras.createView(getTenantId(), {
      name: name.trim(),
      filters: current,
      entity,
      shared: true,
    });
    if (!r.ok) {
      setError(r.error || "Could not save");
      return;
    }
    setName("");
    setNaming(false);
    if (r.view) setActiveId(r.view.id);
    await load();
  }

  async function remove(v: SavedView) {
    if (!confirm(`Delete view "${v.name}"?`)) return;
    await crmExtras.deleteView(getTenantId(), v.id);
    if (activeId === v.id) setActiveId("");
    await load();
  }

  async function makeDefault(v: SavedView) {
    await crmExtras.updateView(getTenantId(), v.id, { is_default: true });
    await load();
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs uppercase tracking-wide text-ink-faint">Views</span>

      {views.map((v) => (
        <span
          key={v.id}
          className={`group inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs ${
            activeId === v.id
              ? "border-brand-500 bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-300"
              : "border-line bg-surface-2 text-ink-muted"
          }`}
        >
          <button aria-label="Action"
            type="button"
            onClick={() => {
              setActiveId(v.id);
              onApply(v.filters as T);
            }}
            title={v.user_id ? "Private to you" : "Shared with the workspace"}
          >
            {v.name}
            {v.is_default ? " ★" : ""}
          </button>
          {!v.is_default && (
            <button aria-label="Action"
              type="button"
              onClick={() => makeDefault(v)}
              title="Make default"
              className="hidden text-ink-faint hover:text-ink group-hover:inline"
            >
              ☆
            </button>
          )}
          <button aria-label="Action"
            type="button"
            onClick={() => remove(v)}
            title="Delete view"
            className="hidden text-ink-faint hover:text-rust-500 group-hover:inline"
          >
            ×
          </button>
        </span>
      ))}

      {naming ? (
        <span className="inline-flex items-center gap-1">
          <input
            autoFocus
            className="w-40 rounded-lg border border-line bg-surface px-2 py-1 text-xs text-ink"
            placeholder="Tier A · not contacted"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") save();
              if (e.key === "Escape") setNaming(false);
            }}
          />
          <button aria-label="Action" type="button" onClick={save} className="text-xs font-medium text-brand-600 dark:text-brand-300">
            Save
          </button>
          <button aria-label="Action" type="button" onClick={() => setNaming(false)} className="text-xs text-ink-faint">
            Cancel
          </button>
        </span>
      ) : (
        <button aria-label="Action"
          type="button"
          onClick={() => setNaming(true)}
          className="rounded-full border border-dashed border-line px-2.5 py-1 text-xs text-ink-muted hover:text-ink"
        >
          + Save current
        </button>
      )}

      {error && <span className="text-xs text-rust-500 dark:text-rust-300">{error}</span>}
    </div>
  );
}
