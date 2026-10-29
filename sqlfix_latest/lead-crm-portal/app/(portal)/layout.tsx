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
import { NotificationBell } from "@/components/layout/NotificationBell";
import { Icon } from "@/components/ui/Icon";
import Link from "next/link";

const PUBLIC_PATHS = ["/login", "/signup", "/accept-invite", "/verify-email"];

const ROUTE_LABELS: Record<string, string> = {
  dashboard: "Overview", growth: "Growth", today: "Today", intelligence: "Intelligence", agents: "Agents", approvals: "Approvals",
  search: "Discover", leads: "Leads", companies: "Accounts", contacts: "Contacts", deals: "Deals", pipeline: "Lead lifecycle",
  sequences: "Sequences", automations: "Automations", compose: "Write email", replies: "Replies", activities: "Activity",
  reports: "Reports", revenue: "Revenue", logs: "Logs", team: "Team", settings: "Settings", integrations: "Integrations",
  billing: "Billing", email: "Email", notifications: "Notifications", docs: "Docs", import: "Import", onboarding: "Onboarding",
};

function crumbs(path: string | null): Array<{ label: string; href?: string }> {
  const parts = (path || "").split("/").filter(Boolean);
  if (!parts.length) return [];
  return parts.map((part, index) => {
    const label = ROUTE_LABELS[part] || (index > 0 && parts[index - 1] === "leads" ? "Lead detail" : part.replace(/-/g, " "));
    return index < parts.length - 1 ? { label, href: "/" + parts.slice(0, index + 1).join("/") } : { label };
  });
}

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
        <main id="main-content" className="min-w-0 flex-1 pt-12 md:pt-0">
          <div className="topbar sticky top-0 z-30 hidden h-14 items-center justify-between gap-4 px-6 md:flex lg:px-8">
            <QuietBreadcrumbs items={[{ label: "Workspace", href: "/dashboard" }, ...crumbs(path)]} />
            <div className="flex items-center gap-2">
              <RealtimeStatus />
              <Link href="/search" className="quiet-action"><Icon name="search" size={14} /><span>Search prospects</span><span className="kbd">⌘K</span></Link>
              <NotificationBell />
              <Link href="/pricing" className="btn-accent btn-sm">Upgrade</Link>
            </div>
          </div>
          {children}
        </main>
      </div>
    </SessionGate>
  );
}
