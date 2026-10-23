"use client";

import { useRef } from "react";
import { useLiveLoader } from "@/lib/live";

/**
 * Bounded visibility-aware freshness monitor for client workspaces. Screens
 * still own their authoritative fetch and realtime subscriptions; this helper
 * only provides an optional cheap probe and never reloads the application.
 */
export function LiveWorkspaceRefresh({
  probe,
  onChange,
  pollMs = 30_000,
}: {
  probe?: () => Promise<unknown>;
  onChange?: () => void;
  pollMs?: number;
}) {
  const previous = useRef("");
  const cb = useRef(onChange);
  cb.current = onChange;
  const check = async () => {
    if (!probe) return;
    const value = await probe();
    const next = JSON.stringify(value);
    if (previous.current && next !== previous.current) cb.current?.();
    previous.current = next;
  };
  useLiveLoader(check, probe ? pollMs : 0);
  return null;
}
