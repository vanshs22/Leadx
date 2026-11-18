"use client";

import { useEffect, useState } from "react";
import { crmTools } from "@/lib/api";

type Committee = Awaited<ReturnType<typeof crmTools.committee>>;
const ROLE: Record<string, string> = { economic_buyer: "Decides budget", operational_decider: "Runs it day to day", influencer: "Influences", gatekeeper: "Gatekeeper", user: "User", unknown: "Role unknown" };

export function BuyingCommittee({ tenantId, companyId }: { tenantId: string; companyId: string }) {
  const [data, setData] = useState<Committee | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let off = false;
    crmTools.committee(tenantId, companyId).then((d) => { if (!off) setData(d); }).catch((e) => { if (!off) setError(e instanceof Error ? e.message : "Could not load the buying committee."); });
    return () => { off = true; };
  }, [tenantId, companyId]);
  if (error) return <p role="alert" className="text-[0.8125rem] text-rust-600 dark:text-rust-300">{error}</p>;
  if (!data) return <p className="text-[0.8125rem] text-ink-faint" aria-busy="true">Loading buying committee…</p>;
  return (
    <div className="text-[0.8125rem]">
      <div className="section-title">Buying committee · coverage {data.coverage_score}/100</div>
      {data.members.length === 0 ? <p className="mt-1 text-ink-muted">No contacts on this account yet.</p> : (
        <ul className="mt-2 grid gap-1 sm:grid-cols-2">
          {data.members.map((m, i) => (
            <li key={i} className="flex items-center justify-between gap-2 rounded-md border border-line px-2.5 py-1.5">
              <span><span className="font-medium">{m.name}</span>{m.title ? <span className="text-ink-muted"> · {m.title}</span> : null}</span>
              <span className="text-xs text-ink-faint">{ROLE[m.role] || m.role}{m.reachable ? "" : " · no contact info"}</span>
            </li>
          ))}
        </ul>
      )}
      {data.gaps.length > 0 && <p className="mt-2 text-ink-muted">Missing: {data.gaps.map((g) => g.look_for).join("; ")}.</p>}
    </div>
  );
}
