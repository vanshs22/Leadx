"use client";

/**
 * Notification bell — one component, both consoles.
 *
 * Polls the unread count rather than holding a socket open: the counts
 * change on the order of minutes (a delivery finishing, a reply landing),
 * so a 60s poll is indistinguishable from realtime here and costs nothing
 * to operate.
 */
import { setVisibleInterval, clearVisibleInterval } from "@/lib/live";
import Link from "next/link";
import { useEffect, useState } from "react";

import { adminExtras } from "@/lib/admin";
import { crmExtras } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";

const POLL_MS = 90_000; // safety net only — realtime pushes changes instantly

export function NotificationBell({ scope = "tenant" }: { scope?: "tenant" | "admin" }) {
  const [unread, setUnread] = useState(0);
  const href = scope === "admin" ? "/admin/notifications" : "/notifications";

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const d =
          scope === "admin"
            ? await adminExtras.notifications(true)
            : await crmExtras.notifications(getTenantId(), true);
        if (alive) setUnread(d?.unread || 0);
      } catch {
        /* a failed poll is not worth showing the user */
      }
    }
    load();
    const t = setVisibleInterval(load, POLL_MS);
    window.addEventListener("leadx:notifications-changed", load);
    return () => {
      alive = false;
      clearVisibleInterval(t);
      window.removeEventListener("leadx:notifications-changed", load);
    };
  }, [scope]);

  // Tenant scope: a notifications row changing in Postgres updates the badge immediately.
  useCrmChange(() => { window.dispatchEvent(new Event("leadx:notifications-changed")); }, { tables: ["notifications"] });

  return (
    <Link
      href={href}
      title={unread ? `${unread} unread` : "Notifications"}
      className="relative inline-flex h-8 w-8 items-center justify-center rounded-lg border border-line bg-surface text-ink-muted hover:bg-surface-2 hover:text-ink"
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" className="h-4 w-4">
        <path d="M15 17h5l-1.4-2.1A2 2 0 0 1 18 13.8V11a6 6 0 1 0-12 0v2.8a2 2 0 0 1-.6 1.1L4 17h11Z" />
        <path d="M9.5 20a2.5 2.5 0 0 0 5 0" />
      </svg>
      {unread > 0 && (
        <span className="absolute -right-1 -top-1 min-w-[16px] rounded-full bg-rust-600 px-1 text-[10px] font-semibold leading-4 text-white">
          {unread > 99 ? "99+" : unread}
        </span>
      )}
    </Link>
  );
}
