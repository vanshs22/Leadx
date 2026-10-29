"use client";

import { useEffect, useState } from "react";
import { AdminSidebar } from "@/components/layout/AdminSidebar";
import { usePathname, useRouter } from "next/navigation";
import { hasAdminSession } from "@/lib/admin";
import { QuietBreadcrumbs } from "@/components/ui/Primitives";
import { NotificationBell } from "@/components/layout/NotificationBell";
import { Icon } from "@/components/ui/Icon";
import Link from "next/link";

const ADMIN_LABELS: Record<string, string> = {
  tenants: "Clients", health: "Tenant health", territory: "Territory", icps: "ICPs", search: "Search & email", agents: "Agent operations", pipeline: "Pipeline",
  runs: "Run history", leads: "Leads", jobs: "Jobs", automation: "Automation", autonomous: "Autonomous growth", prospecting: "Our prospecting",
  notifications: "Notifications", support: "Support inbox", replies: "Replies", suppression: "Suppression", usage: "Usage & billing",
  keys: "API keys", intelligence: "Intelligence", ops: "Ops health", scale: "Scale & reliability", security: "Security & audit",
};

function adminCrumbs(path: string | null): Array<{ label: string; href?: string }> {
  const parts = (path || "").split("/").filter(Boolean).slice(1);
  if (!parts.length) return [{ label: "Overview" }];
  return parts.map((part, index) => {
    const label = ADMIN_LABELS[part] || (index > 0 ? "Detail" : part.replace(/-/g, " "));
    return index < parts.length - 1 ? { label, href: "/admin/" + parts.slice(0, index + 1).join("/") } : { label };
  });
}

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const isLogin = path === "/admin/login";
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (isLogin) return;
    let active = true;
    void hasAdminSession().then((ok) => {
      if (!active) return;
      if (!ok) router.replace("/admin/login");
      else setChecked(true);
    });
    return () => { active = false; };
  }, [isLogin, path, router]);

  if (isLogin) {
    return <div className="min-h-screen bg-surface text-ink">{children}</div>;
  }

  if (!checked) {
    return <div className="min-h-screen bg-surface" />;
  }

  return (
    <>
    <a href="#main-content" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[100] focus:rounded-lg focus:bg-surface focus:px-4 focus:py-2 focus:text-sm focus:shadow-lg">Skip to main content</a>
    <div className="flex min-h-screen bg-surface text-ink">
      <AdminSidebar />
      <main id="main-content" className="operator-main min-w-0 flex-1 pt-12 md:pt-0">
        <div className="operator-topbar topbar sticky top-0 z-30 hidden h-14 items-center justify-between gap-4 px-6 md:flex lg:px-8">
          <QuietBreadcrumbs items={[{ label: "Control plane", href: "/admin" }, ...adminCrumbs(path)]} />
          <div className="flex items-center gap-2">
            <Link href="/admin/health" className="quiet-action"><span className="live-dot" aria-hidden="true" />Platform health</Link>
            <Link href="/admin/support" className="quiet-action"><Icon name="support" size={14} />Support inbox</Link>
            <NotificationBell scope="admin" />
          </div>
        </div>
        <div>{children}</div>
      </main>
    </div>
    </>
  );
}
