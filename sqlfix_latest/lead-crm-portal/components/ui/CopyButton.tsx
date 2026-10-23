"use client";
import { useState } from "react";

export function CopyButton({ value, label = "Copy" }: { value?: string | null; label?: string }) {
  const [ok, setOk] = useState(false);
  if (!value) return null;
  return (
    <button aria-label="Action"
      type="button"
      className="btn-ghost !px-2 !py-0.5 text-xs"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setOk(true);
          setTimeout(() => setOk(false), 1200);
        } catch {}
      }}
    >
      {ok ? "Copied" : label}
    </button>
  );
}
