"use client";

import { usePathname } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { SessionGate } from "@/components/workspace/SessionGate";
import { CommandPalette } from "@/components/CommandPalette";
import { RealtimeStatus } from "@/components/layout/RealtimeStatus";
import { SupportChat } from "@/components/support/SupportChat";
import { crm } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useEffect } from "react";
import { QuietBreadcrumbs } from "@/components/ui/Primitives";

const PUBLIC_PATHS = ["/login", "/signup", "/accept-invite", "/verify-email"];

export default function PortalLayout({ children }: { children: React.ReactNode }) {
  const path = usePathname();

  // Warm the two most frequently visited CRM datasets after the shell is ready.
  // This is idle/low-priority work and uses the API client's short-lived cache,
  // so clicking Leads/Pipeline immediately after Home does not pay another DB round-trip.
  useEffect(() => {
    if (PUBLIC_PATHS.includes(path || "") || (path !== "/dashboard" && path !== "/growth")) return;
    const tid = getTenantId();
    if (!tid) return;
    const warm = () => {
      void Promise.allSettled([
        crm.leads(tid, { limit: 50 }),
        crm.pipeline(tid),
        crm.searchQuota(tid),
      ]);
    };
    const idle = (window as any).requestIdleCallback;
    const id = idle ? idle(warm, { timeout: 900 }) : window.setTimeout(warm, 350);
    return () => {
      if (idle && typeof id === "number") (window as any).cancelIdleCallback?.(id);
      else window.clearTimeout(id);
    };
  }, [path]);

  if (PUBLIC_PATHS.includes(path || "")) {
    return <div className="min-h-screen bg-surface text-ink">{children}</div>;
  }

  return (
    <SessionGate>
      <a href="#main-content" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[100] focus:rounded-lg focus:bg-surface focus:px-4 focus:py-2 focus:text-sm focus:shadow-lg">Skip to main content</a>
      <CommandPalette />
      <SupportChat />
      <div className="flex min-h-screen bg-surface text-ink">
        <Sidebar />
        <div className="pointer-events-none fixed right-4 top-3 z-30 hidden md:block"><RealtimeStatus /></div>
        <main id="main-content" className="min-w-0 flex-1 pt-12 md:pt-0">
          <div className="topbar sticky top-0 z-30 hidden h-12 items-center justify-between px-6 md:flex">
            <QuietBreadcrumbs items={[{ label: "Workspace", href: "/dashboard" }, { label: path === "/dashboard" ? "Overview" : path === "/growth" ? "Growth workspace" : (path?.split("/").filter(Boolean).slice(-1)[0] || "Workspace").replace(/-/g, " ") }]} />
            <div className="flex items-center gap-2">
              <a href="/growth" className="quiet-action"><span>⌁</span><span>Growth workspace</span></a><a href="/search" className="quiet-action"><span>⌕</span><span>Search prospects</span><kbd className="rounded border border-line px-1.5 py-0.5 text-[9px] text-ink-faint">⌘K</kbd></a>
              <a href="/pricing" className="btn-primary px-3 py-1.5 text-xs">Upgrade</a>
            </div>
          </div>
          {children}
        </main>
      </div>
    </SessionGate>
  );
}
