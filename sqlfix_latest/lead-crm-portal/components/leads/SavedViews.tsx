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
import { Icon } from "@/components/ui/Icon";

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
    <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Saved views">
      <span className="label mr-1">Views</span>

      {views.map((v) => {
        const active = activeId === v.id;
        return (
          <span
            key={v.id}
            className={`group inline-flex h-7 items-center gap-0.5 rounded-md border pl-2.5 pr-1 text-xs font-medium transition ${
              active
                ? "border-brand-300 bg-brand-50 text-brand-800 dark:border-brand-500/40 dark:bg-brand-500/15 dark:text-brand-100"
                : "border-line bg-surface-2 text-ink-muted hover:border-line-strong hover:text-ink"
            }`}
          >
            <button
              type="button"
              aria-pressed={active}
              onClick={() => {
                setActiveId(v.id);
                onApply(v.filters as T);
              }}
              title={v.user_id ? "Private to you" : "Shared with the workspace"}
              className="inline-flex max-w-[12rem] items-center gap-1.5 truncate pr-1"
            >
              <span className="truncate">{v.name}</span>
              {v.is_default ? <span className="rounded bg-surface-3 px-1 text-[10px] font-semibold text-ink-faint">Default</span> : null}
            </button>
            {!v.is_default && (
              <button
                type="button"
                onClick={() => makeDefault(v)}
                title="Make default"
                aria-label={`Make ${v.name} the default view`}
                className="hidden h-5 w-5 items-center justify-center rounded text-ink-faint hover:bg-surface-3 hover:text-ink group-hover:inline-flex group-focus-within:inline-flex"
              >
                <Icon name="check" size={12} />
              </button>
            )}
            <button
              type="button"
              onClick={() => remove(v)}
              title="Delete view"
              aria-label={`Delete view ${v.name}`}
              className="hidden h-5 w-5 items-center justify-center rounded text-ink-faint hover:bg-rust-50 hover:text-rust-600 group-hover:inline-flex group-focus-within:inline-flex dark:hover:bg-rust-900/30 dark:hover:text-rust-200"
            >
              <Icon name="close" size={12} />
            </button>
          </span>
        );
      })}

      {naming ? (
        <span className="inline-flex items-center gap-1">
          <input
            autoFocus
            aria-label="Saved view name"
            className="input h-7 w-44 py-1 text-xs"
            placeholder="Tier A · not contacted"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") save();
              if (e.key === "Escape") setNaming(false);
            }}
          />
          <button type="button" onClick={save} className="btn-primary btn-sm">
            Save
          </button>
          <button type="button" onClick={() => setNaming(false)} className="btn-ghost btn-sm">
            Cancel
          </button>
        </span>
      ) : (
        <button
          type="button"
          onClick={() => setNaming(true)}
          className="inline-flex h-7 items-center gap-1 rounded-md border border-dashed border-line-strong px-2.5 text-xs font-medium text-ink-muted transition hover:border-ink-faint hover:text-ink"
        >
          <Icon name="plus" size={12} />
          Save current
        </button>
      )}

      {error && <span role="alert" className="text-xs text-rust-600 dark:text-rust-300">{error}</span>}
    </div>
  );
}
