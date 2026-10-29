"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { NotificationBell } from "@/components/layout/NotificationBell";
import { ThemeToggle } from "@/components/theme/ThemeProvider";
import { signOut } from "@/lib/auth";
import { Icon, type IconName } from "@/components/ui/Icon";

type NavItem = { href: string; label: string; icon: IconName; exact?: boolean };

const GROUPS: { title: string; items: NavItem[] }[] = [
  { title: "Operations", items: [
    { href: "/admin", label: "Overview", icon: "home", exact: true },
    { href: "/admin/tenants", label: "Clients", icon: "clients" },
    { href: "/admin/health", label: "Tenant health", icon: "health" },
    { href: "/admin/territory", label: "Territory", icon: "territory" },
    { href: "/admin/icps", label: "ICPs", icon: "icp" },
  ] },
  { title: "Lead engine", items: [
    { href: "/admin/agents", label: "Agent operations", icon: "spark" },
    { href: "/admin/search", label: "Search & email", icon: "search" },
    { href: "/admin/pipeline", label: "Pipeline", icon: "pipeline" },
    { href: "/admin/runs", label: "Run history", icon: "run" },
    { href: "/admin/leads", label: "Leads", icon: "leads" },
    { href: "/admin/jobs", label: "Jobs", icon: "job" },
    { href: "/admin/automation", label: "Automation", icon: "automation" },
    { href: "/admin/autonomous", label: "Autonomous growth", icon: "growth" },
    { href: "/admin/prospecting", label: "Our prospecting", icon: "spark" },
  ] },
  { title: "Engagement", items: [
    { href: "/admin/notifications", label: "Notifications", icon: "bell" },
    { href: "/admin/support", label: "Support inbox", icon: "support" },
    { href: "/admin/replies", label: "Replies", icon: "reply" },
    { href: "/admin/suppression", label: "Suppression", icon: "suppression" },
  ] },
  { title: "Platform", items: [
    { href: "/admin/usage", label: "Usage & billing", icon: "usage" },
    { href: "/admin/keys", label: "API keys", icon: "key" },
    { href: "/admin/intelligence", label: "Intelligence", icon: "brain" },
    { href: "/admin/ops", label: "Ops health", icon: "activity" },
    { href: "/admin/scale", label: "Scale & reliability", icon: "scale" },
    { href: "/admin/security", label: "Security & audit", icon: "security" },
  ] },
];

export function AdminSidebar() {
  const path = usePathname(); const [open, setOpen] = useState(false); useEffect(() => setOpen(false), [path]);
  useEffect(() => { if (!open) return; const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); }; document.addEventListener("keydown", onKey); const previous = document.body.style.overflow; document.body.style.overflow = "hidden"; return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = previous; }; }, [open]);
  return <>
    <div className="fixed inset-x-0 top-0 z-40 flex h-12 items-center gap-2.5 border-b border-[rgb(30_42_64)] bg-[rgb(var(--rail))] px-3 text-white md:hidden">
      <button aria-label="Open operator navigation" aria-expanded={open} aria-controls="admin-navigation" onClick={() => setOpen(true)} className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-white/15 text-white/80"><Icon name="menu" size={17} /></button>
      <span className="operator-brand-mark flex h-7 w-7 items-center justify-center rounded-md text-[9px] font-bold">LX</span>
      <span className="font-display text-sm font-semibold">LeadX Operator</span><span className="ml-auto"><NotificationBell scope="admin" /></span>
    </div>
    {open && <button aria-label="Close operator navigation" onClick={() => setOpen(false)} className="fixed inset-0 z-40 bg-black/50 md:hidden" />}
    <aside id="admin-navigation" aria-label="Operator control plane navigation" className={`operator-rail fixed inset-y-0 left-0 z-50 flex h-screen w-[244px] shrink-0 flex-col border-r transition-transform md:sticky md:top-0 md:z-auto md:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`}>
      <div className="flex h-14 items-center gap-2.5 border-b border-line px-4">
        <span className="operator-brand-mark flex h-8 w-8 items-center justify-center rounded-lg font-display text-[10px] font-bold">LX</span>
        <div className="min-w-0"><div className="brand-wordmark leading-tight">LeadX</div><div className="text-[11px] text-ink-faint">Control plane</div></div>
        <span className="ml-auto rounded border border-[rgb(219_184_111/.35)] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-[rgb(219_184_111)]">Operator</span>
      </div>
      <nav id="admin-navigation-list" aria-label="Control plane sections" className="min-h-0 flex-1 overflow-y-auto px-3 py-4">
        {GROUPS.map((group) => (
          <div key={group.title} className="mb-5 last:mb-0">
            <div className="nav-section">{group.title}</div>
            <div className="space-y-px">
              {group.items.map((item) => {
                const active = item.exact ? path === item.href : path === item.href || Boolean(path?.startsWith(item.href + "/"));
                return <Link key={item.href} href={item.href} aria-current={active ? "page" : undefined} onClick={() => setOpen(false)} className={active ? "sidebar-link-active" : "sidebar-link"}><Icon name={item.icon} size={16} className={active ? "" : "opacity-70"} /><span>{item.label}</span></Link>;
              })}
            </div>
          </div>
        ))}
      </nav>
      <div className="space-y-1 border-t border-line p-3">
        <ThemeToggle className="w-full" />
        <button aria-label="Sign out" className="btn-ghost btn-sm w-full justify-start" onClick={async () => { await signOut().catch(() => {}); window.location.href = "/admin/login"; }}><Icon name="logout" size={14} />Sign out</button>
      </div>
    </aside>
  </>;
}
