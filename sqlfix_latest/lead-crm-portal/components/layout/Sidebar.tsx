"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { NotificationBell } from "@/components/layout/NotificationBell";
import { ThemeToggle } from "@/components/theme/ThemeProvider";
import { getTenantName, clearTenant } from "@/lib/tenant";
import { signOut } from "@/lib/auth";
import { Icon, type IconName } from "@/components/ui/Icon";

const GROUPS = [
  { title: "Workspace", items: [["/dashboard", "Overview", "home"], ["/growth", "Growth", "growth"], ["/today", "Today", "clock"], ["/intelligence", "Intelligence", "brain"], ["/agents", "Agents", "spark"], ["/approvals", "Approvals", "check"], ["/search", "Discover", "search"], ["/leads", "Leads", "leads"], ["/companies", "Accounts", "clients"], ["/contacts", "Contacts", "team"], ["/deals", "Deals", "deal"], ["/pipeline", "Lead lifecycle", "pipeline"]] },
  { title: "Engage", items: [["/sequences", "Sequences", "sequence"], ["/automations", "Automations", "automation"], ["/compose", "Write email", "compose"], ["/replies", "Replies", "reply"], ["/activities", "Activity", "activity"]] },
  { title: "Measure", items: [["/reports", "Reports", "report"], ["/revenue", "Revenue", "revenue"], ["/reports/deals", "Deal revenue", "deal"], ["/logs", "Logs", "activity"]] },
  { title: "Workspace settings", items: [["/team", "Team", "team"], ["/settings/integrations", "Integrations", "integration"], ["/settings/billing", "Billing", "billing"], ["/settings", "Settings", "settings"]] },
] as const;

function NavIcon({ value }: { value: IconName }) { return <span className="nav-icon flex h-5 w-5 items-center justify-center text-current opacity-75"><Icon name={value} size={16} /></span>; }

export function Sidebar() {
  const path = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [path]);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = previous; };
  }, [open]);
  async function handleSignOut() { try { clearTenant(); await signOut(); } catch {} router.replace("/login"); }
  return <>
    <div className="fixed inset-x-0 top-0 z-40 flex h-12 items-center gap-2 border-b border-line bg-[rgb(var(--sidebar))] px-3 md:hidden">
      <button type="button" aria-label="Open navigation" aria-expanded={open} aria-controls="client-navigation" onClick={() => setOpen(true)} className="icon-button h-8 w-8"><Icon name="menu" size={17} /></button>
      <span className="brand-mark h-7 w-7 rounded-lg text-[8px]">LX</span><span className="text-sm font-semibold">LeadX</span><span className="ml-auto"><NotificationBell /></span>
    </div>
    {open && <button type="button" aria-label="Close navigation" onClick={() => setOpen(false)} className="fixed inset-0 z-40 bg-slate-950/35 md:hidden" />}
    <aside id="client-navigation" aria-label="Client workspace navigation" className={`workspace-rail fixed inset-y-0 left-0 z-50 flex h-screen w-[248px] flex-col border-r border-[rgb(var(--sidebar-border))] transition-transform md:sticky md:top-0 md:z-auto md:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`}>
      <div className="border-b border-line px-4 py-4"><Link href="/dashboard" className="flex items-center gap-2.5" onClick={() => setOpen(false)}><span className="brand-mark h-8 w-8 rounded-[9px] text-[9px]">LX</span><span className="brand-wordmark">LeadX</span></Link><div className="mt-3 flex items-center justify-between rounded-lg border border-line/80 bg-surface-2 px-2.5 py-2"><div className="min-w-0"><div className="truncate text-xs font-semibold text-ink">{typeof window !== "undefined" ? getTenantName() || "Workspace" : "Workspace"}</div><div className="text-[10px] text-ink-faint">Client workspace</div></div><span className="live-dot" style={{ width: 7, height: 7 }} /></div></div>
      <nav className="min-h-0 flex-1 overflow-y-auto px-3 py-4">
        {GROUPS.map((group) => <div key={group.title} className="mb-5 last:mb-0"><div className="px-2 pb-1.5 text-[9px] font-bold uppercase tracking-[.14em] text-ink-faint">{group.title}</div><div className="space-y-0.5">{group.items.map(([href, label, icon]) => { const active = path === href || (href !== "/reports" && path?.startsWith(href + "/")); return <Link key={href} href={href} aria-current={active ? "page" : undefined} onClick={() => setOpen(false)} className={`sidebar-link ${active ? "sidebar-link-active" : ""}`}><NavIcon value={icon} /><span>{label}</span></Link>; })}</div></div>)}
      </nav>
      <div className="border-t border-line p-3"><Link href="/search" onClick={() => setOpen(false)} className="mb-2 flex items-center justify-between rounded-lg border border-line bg-surface-2 px-3 py-2.5 text-xs font-semibold text-ink hover:border-brand-300">Find prospects <span className="text-ink-faint">⌘K</span></Link><div className="mb-2"><ThemeToggle className="w-full" /></div><button aria-label="Sign out" type="button" onClick={handleSignOut} className="w-full rounded-lg px-2.5 py-2 text-left text-xs font-medium text-ink-muted hover:bg-surface-3 hover:text-red-600">Sign out</button></div>
    </aside>
  </>;
}
