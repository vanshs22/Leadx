"use client";
import { useState } from "react";
import { Icon } from "./Icon";

export function CopyButton({ value, label = "Copy" }: { value?: string | null; label?: string }) {
  const [ok, setOk] = useState(false);
  if (!value) return null;
  return (
    <button aria-label={label}
      type="button"
      className={`inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-[11px] font-medium transition-colors ${ok ? "text-emerald-700 dark:text-emerald-300" : "text-ink-faint hover:bg-surface-3 hover:text-ink"}`}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setOk(true);
          setTimeout(() => setOk(false), 1200);
        } catch {}
      }}
    >
      {ok ? <Icon name="check" size={12} /> : null}
      {ok ? "Copied" : label}
    </button>
  );
}
