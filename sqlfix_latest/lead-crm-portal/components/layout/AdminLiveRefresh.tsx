"use client";

import { useRef } from "react";
import { admin } from "@/lib/admin";
import { useLiveLoader } from "@/lib/live";

/**
 * Admin freshness monitor.
 *
 * Polls one cheap aggregate (only while the tab is visible) and, when it
 * changes, asks the layout to SOFT-refresh the current screen. There is no
 * full page reload any more: sidebar, scroll position of the shell and the
 * session stay intact, and nothing happens while the operator is typing.
 */
export function AdminLiveRefresh({ onChange, onDeadline }: { onChange?: () => void; onDeadline?: () => void }) {
  const previous = useRef("");
  const pendingEdit = useRef(false);
  const cb = useRef(onChange);
  const deadline = useRef(onDeadline);
  cb.current = onChange;
  deadline.current = onDeadline;

  const check = async () => {
    try {
      const snapshot = await admin.globalStats();
      const next = JSON.stringify(snapshot);
      const changed = !!previous.current && next !== previous.current;
      previous.current = next;
      const a = document.activeElement as HTMLElement | null;
      const editing = !!a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.tagName === "SELECT" || a.isContentEditable);
      if (changed || pendingEdit.current) {
        if (editing) { pendingEdit.current = true; return; }
        pendingEdit.current = false;
        cb.current?.();
      }
    } catch {
      // Pages own their terminal error state. The monitor is only a freshness signal.
    }
  };

  useLiveLoader(check, 30_000, [], { onDeadline: () => deadline.current?.() });
  return null;
}
