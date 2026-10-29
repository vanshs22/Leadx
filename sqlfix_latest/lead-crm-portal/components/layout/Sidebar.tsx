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
  { title: "Workspace", items: [["/dashboard", "Overview", "home"], ["/growth", "Growth", "growth"], ["/today", "Today", "clock"], ["/intelligence", "Intelligence", "brain"], ["/approvals", "Approvals", "check"]] },
  { title: "Prospects & CRM", items: [["/search", "Discover", "search"], ["/leads", "Leads", "leads"], ["/companies", "Accounts", "clients"], ["/contacts", "Contacts", "team"], ["/deals", "Deals", "deal"], ["/pipeline", "Lead lifecycle", "pipeline"]] },
  { title: "Engage", items: [["/sequences", "Sequences", "sequence"], ["/automations", "Automations", "automation"], ["/compose", "Write email", "compose"], ["/replies", "Replies", "reply"], ["/activities", "Activity", "activity"]] },
  { title: "Measure", items: [["/reports", "Reports", "report"], ["/revenue", "Revenue", "revenue"], ["/reports/deals", "Deal revenue", "deal"], ["/logs", "Logs", "job"]] },
  { title: "Settings", items: [["/team", "Team", "team"], ["/settings/integrations", "Integrations", "integration"], ["/settings/billing", "Billing", "billing"], ["/settings", "Workspace settings", "settings"]] },
] as const;

function isActive(path: string | null, href: string) {
  if (!path) return false;
  if (path === href) return true;
  // Parent routes stay highlighted on their detail pages, but sibling routes
  // with their own nav entry (e.g. /reports/deals, /settings/billing) win.
  if (href === "/reports" || href === "/settings") return false;
  return path.startsWith(href + "/");
}

export function Sidebar() {
  const path = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [tenantName, setTenantName] = useState("Workspace");
  useEffect(() => setOpen(false), [path]);
  useEffect(() => { setTenantName(getTenantName() || "Workspace"); }, []);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = previous; };
  }, [open]);
  async function handleSignOut() { try { clearTenant(); await signOut(); } catch {} router.replace("/login"); }
  const initials = tenantName.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("") || "W";

  return <>
    <div className="fixed inset-x-0 top-0 z-40 flex h-12 items-center gap-2.5 border-b border-line bg-surface-2 px-3 md:hidden">
      <button type="button" aria-label="Open navigation" aria-expanded={open} aria-controls="client-navigation" onClick={() => setOpen(true)} className="icon-button h-8 w-8"><Icon name="menu" size={17} /></button>
      <span className="brand-mark h-7 w-7 text-[9px]">LX</span><span className="brand-wordmark">LeadX</span><span className="ml-auto"><NotificationBell /></span>
    </div>
    {open && <button type="button" aria-label="Close navigation" onClick={() => setOpen(false)} className="fixed inset-0 z-40 bg-[rgb(10_15_25/.4)] md:hidden" />}
    <aside id="client-navigation" aria-label="Client workspace navigation" className={`workspace-rail fixed inset-y-0 left-0 z-50 flex h-screen w-[244px] shrink-0 flex-col border-r border-[rgb(var(--sidebar-border))] transition-transform md:sticky md:top-0 md:z-auto md:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`}>
      <div className="flex h-14 items-center gap-2.5 border-b border-line px-4">
        <Link href="/dashboard" className="flex items-center gap-2.5" onClick={() => setOpen(false)}><span className="brand-mark">LX</span><span className="brand-wordmark">LeadX</span></Link>
        <span className="badge-neutral ml-auto">Client</span>
      </div>
      <div className="px-3 pt-3">
        <div className="flex items-center gap-2.5 rounded-lg border border-line bg-surface px-2.5 py-2">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-signal-100 text-[11px] font-semibold text-signal-800 dark:bg-signal-400/15 dark:text-signal-200">{initials}</span>
          <div className="min-w-0 flex-1"><div className="truncate text-[0.8125rem] font-semibold text-ink">{tenantName}</div><div className="text-[11px] text-ink-faint">Client workspace</div></div>
          <span className="live-dot" aria-hidden="true" />
        </div>
      </div>
      <nav aria-label="Workspace sections" className="min-h-0 flex-1 overflow-y-auto px-3 py-4">
        {GROUPS.map((group) => (
          <div key={group.title} className="mb-5 last:mb-0">
            <div className="nav-section">{group.title}</div>
            <div className="space-y-px">
              {group.items.map(([href, label, icon]) => {
                const active = isActive(path, href);
                return <Link key={href} href={href} aria-current={active ? "page" : undefined} onClick={() => setOpen(false)} className={active ? "sidebar-link-active" : "sidebar-link"}><Icon name={icon as IconName} size={16} className={active ? "opacity-100" : "opacity-70"} /><span>{label}</span></Link>;
              })}
            </div>
          </div>
        ))}
      </nav>
      <div className="space-y-1 border-t border-line p-3">
        <Link href="/search" onClick={() => setOpen(false)} className="mb-1 flex items-center justify-between rounded-lg border border-line bg-surface px-3 py-2 text-xs font-semibold text-ink hover:border-line-strong"><span className="flex items-center gap-2"><Icon name="search" size={14} />Find prospects</span><span className="kbd">⌘K</span></Link>
        <ThemeToggle className="w-full" />
        <button aria-label="Sign out" type="button" onClick={handleSignOut} className="btn-ghost btn-sm w-full justify-start hover:text-rust-600"><Icon name="logout" size={14} />Sign out</button>
      </div>
    </aside>
  </>;
}
