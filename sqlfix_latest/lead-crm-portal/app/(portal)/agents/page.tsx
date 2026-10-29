"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { hasAdminSession } from "@/lib/admin";
import { ErrorState } from "@/components/ui/States";
import { PageHeader } from "@/components/ui/Primitives";

/**
 * Agent registry/run controls are platform administration surfaces. Keep the
 * client route as a safe compatibility guard rather than exposing a second,
 * tenant-facing control plane.
 */
export default function AgentsPage() {
  const [checking, setChecking] = useState(true);
  const [allowed, setAllowed] = useState(false);

  useEffect(() => {
    let active = true;
    void hasAdminSession().then((isAdmin) => {
      if (!active) return;
      setAllowed(isAdmin);
      setChecking(false);
      if (isAdmin) window.location.replace("/admin/agents");
    });
    return () => { active = false; };
  }, []);

  if (checking) {
    return <div className="page" aria-busy="true"><PageHeader eyebrow="Workspace" title="Agents" description="Checking access to the platform administration surface." /></div>;
  }

  if (allowed) return <div className="page" aria-busy="true"><PageHeader eyebrow="Control plane" title="Opening agent operations" description="Redirecting to the authorized administration surface." /></div>;

  return (
    <div className="page">
      <PageHeader eyebrow="Workspace" title="Agents" description="Agent registry and run controls are available only to authorized platform administrators." />
      <ErrorState error={new Error("You do not have permission to view agent operations.")} onRetry={() => window.location.assign("/dashboard")} />
      <Link href="/dashboard" className="btn-secondary mt-3 inline-flex">Return to workspace</Link>
    </div>
  );
}
