"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { NotificationBell } from "@/components/layout/NotificationBell";
import { ThemeToggle } from "@/components/theme/ThemeProvider";
import { signOut } from "@/lib/auth";
import { Icon, type IconName } from "@/components/ui/Icon";

const NAV: { href: string; label: string; icon: IconName; exact?: boolean }[] = [
  { href: "/admin", label: "Overview", icon: "home", exact: true },
  { href: "/admin/tenants", label: "Clients", icon: "clients" },
  { href: "/admin/health", label: "Tenant health", icon: "health" },
  { href: "/admin/territory", label: "Territory", icon: "territory" },
  { href: "/admin/icps", label: "ICPs", icon: "icp" },
  { href: "/admin/search", label: "Search & email", icon: "search" },
  { href: "/admin/pipeline", label: "Pipeline", icon: "pipeline" },
  { href: "/admin/automation", label: "Automation", icon: "automation" },
  { href: "/admin/autonomous", label: "Autonomous growth", icon: "growth" },
  { href: "/admin/runs", label: "Run history", icon: "run" },
  { href: "/admin/leads", label: "Leads", icon: "leads" },
  { href: "/admin/jobs", label: "Jobs", icon: "job" },
  { href: "/admin/notifications", label: "Notifications", icon: "bell" },
  { href: "/admin/support", label: "Support inbox", icon: "support" },
  { href: "/admin/replies", label: "Replies", icon: "reply" },
  { href: "/admin/prospecting", label: "Our prospecting", icon: "search" },
  { href: "/admin/keys", label: "API keys", icon: "key" },
  { href: "/admin/usage", label: "Usage & billing", icon: "usage" },
  { href: "/admin/suppression", label: "Suppression", icon: "suppression" },
  { href: "/admin/intelligence", label: "Intelligence", icon: "brain" },
  { href: "/admin/ops", label: "Ops health", icon: "health" },
  { href: "/admin/scale", label: "Scale & reliability", icon: "scale" },
  { href: "/admin/security", label: "Security & audit", icon: "security" },
];

// Operator view uses the signal-teal accent (distinct from the client
// portal's brass) — a deliberate cue you're in the control room, not
// the client-facing product.
export function AdminSidebar() {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [path]);
  return (
    <>
      <div className="fixed inset-x-0 top-0 z-40 flex items-center border-b border-line bg-[rgb(var(--sidebar))] px-3 py-2 md:hidden">
        <button aria-label="Open operator navigation" onClick={() => setOpen(true)} className="h-8 w-8 rounded-lg border border-line bg-surface text-ink"><Icon name="menu" size={17} /></button>
        <span className="ml-3 font-display text-sm font-semibold text-ink">LeadX Operator</span>
        <span className="ml-auto"><NotificationBell scope="admin" /></span>
      </div>
      {open && <button aria-label="Close operator navigation" onClick={() => setOpen(false)} className="fixed inset-0 z-40 bg-black/40 md:hidden" />}
      <aside className={`operator-rail fixed inset-y-0 left-0 z-50 flex h-screen w-64 shrink-0 flex-col border-r transition-transform md:sticky md:top-0 md:z-auto md:w-60 md:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`}>
      <div className="border-b border-line px-4 py-5 pt-14 md:pt-5">
        <div className="mb-3 flex items-center gap-2">
          <div className="operator-brand-mark flex h-8 w-8 items-center justify-center rounded-xl text-xs font-black text-white">LX</div>
          <div><div className="text-[10px] font-bold uppercase tracking-[0.14em] text-brand-700 dark:text-brand-300">LeadX</div><div className="text-[11px] text-ink-faint">Control plane</div></div>
        </div>
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="font-mono text-[10px] font-semibold uppercase tracking-[0.15em] text-signal-600 dark:text-signal-400">
              Operator
            </div>
            <div className="mt-1 font-display text-sm font-semibold text-ink">Lead Admin</div>
          </div>
          <NotificationBell scope="admin" />
        </div>
      </div>
      <nav className="flex-1 space-y-0.5 overflow-y-auto p-3">
        <div className="mb-2 px-3 pt-1 text-[9px] font-bold uppercase tracking-[0.16em] text-ink-faint">Operations</div>
        {NAV.map((item, index) => {
          const active = item.exact ? path === item.href : path?.startsWith(item.href);
          return (
            <div key={item.href}>
            {index === 5 && <div className="mb-2 mt-4 px-3 pt-1 text-[9px] font-bold uppercase tracking-[0.16em] text-ink-faint">Revenue & intelligence</div>}
            <Link
              href={item.href}
              className={
                active
                  ? "flex items-center gap-2 rounded-xl2 bg-signal-50 px-3 py-2 text-sm font-medium text-signal-800 dark:bg-signal-500/15 dark:text-signal-300"
                  : "flex items-center gap-2 rounded-xl2 px-3 py-2 text-sm text-ink-muted hover:bg-surface-3 hover:text-ink"
              }
            >
              <Icon name={item.icon} size={16} />
              <span>{item.label}</span>
            </Link>
            </div>
          );
        })}
      </nav>
      <div className="space-y-2 border-t border-line p-3">
        <ThemeToggle className="w-full" />
        <button aria-label="Action"
          className="btn-ghost w-full text-xs"
          onClick={async () => {
            await signOut().catch(() => {});
            window.location.href = "/admin/login";
          }}
        >
          Sign out
        </button>
      </div>
      </aside>
    </>
  );
}
