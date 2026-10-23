"use client";

import { useEffect, useState } from "react";
import { AdminSidebar } from "@/components/layout/AdminSidebar";
import { usePathname, useRouter } from "next/navigation";
import { hasAdminSession } from "@/lib/admin";

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
      <main id="main-content" className="operator-main min-w-0 flex-1 bg-surface/70"><div className="operator-topbar topbar sticky top-0 z-30 flex h-12 items-center justify-between px-5"><div className="hidden items-center gap-2 md:flex"><span className="brand-mark h-7 w-7 rounded-lg text-[9px]">LX</span><span className="brand-wordmark">LeadX</span><span className="eyebrow ml-2">Control plane</span></div><div className="ml-auto flex items-center gap-2"><span className="rounded-full border border-signal-200 bg-signal-50 px-2.5 py-1 text-[10px] font-bold text-signal-700 dark:border-signal-500/20 dark:bg-signal-500/10 dark:text-signal-300">● Systems online</span><a href="/admin/support" className="btn-ghost px-2.5 py-1.5 text-xs">Support inbox</a></div></div><div>{children}</div></main>
    </div>
    </>
  );
}
