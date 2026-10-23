"use client";

/**
 * ⌘K / Ctrl+K palette: jump to a page, or straight to a record.
 *
 * Record search reuses the list endpoints the pages already call
 * (crm.leads + crmEntities.list*), all of which accept `search`. It deliberately
 * does NOT use crm.searchRun — that endpoint runs a live scrape, takes 30–120s
 * and burns the tenant's search quota, so it is unusable as a typeahead.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { crm } from "@/lib/api";
import { crmEntities, contactName } from "@/lib/crm";
import { getTenantId } from "@/lib/tenant";

interface Item {
  key: string;
  label: string;
  hint?: string;
  href: string;
}

const NAV: Item[] = [
  { key: "nav:/dashboard", label: "Dashboard", hint: "Overview & next steps", href: "/dashboard" },
  { key: "nav:/leads", label: "Leads", hint: "Call, email, export", href: "/leads" },
  { key: "nav:/contacts", label: "Contacts", hint: "People", href: "/contacts" },
  { key: "nav:/companies", label: "Companies", hint: "Accounts", href: "/companies" },
  { key: "nav:/deals", label: "Deals", hint: "Kanban board", href: "/deals" },
  { key: "nav:/pipeline", label: "Pipeline", hint: "Lead stages", href: "/pipeline" },
  { key: "nav:/activities", label: "Activities", hint: "Calls & notes", href: "/activities" },
  { key: "nav:/sequences", label: "Sequences", hint: "Follow-up automation", href: "/sequences" },
  { key: "nav:/replies", label: "Replies", hint: "Inbound, categorized", href: "/replies" },
  { key: "nav:/reports", label: "Reports", hint: "Wins & quality", href: "/reports" },
  { key: "nav:/reports/deals", label: "Deal revenue", hint: "Forecast & attribution", href: "/reports/deals" },
  { key: "nav:/compose", label: "Write email", hint: "Proven copy + rewrite", href: "/compose" },
  { key: "nav:/import", label: "Import", hint: "CSV of your own list", href: "/import" },
  { key: "nav:/settings", label: "Settings", hint: "Account & weights", href: "/settings" },
  { key: "nav:/team", label: "Team", hint: "Members & roles", href: "/team" },
];

/** Subsequence match: "dlrv" hits "Deal revenue". Falls back to plain substring. */
function matches(haystack: string, needle: string): boolean {
  const h = haystack.toLowerCase();
  const n = needle.toLowerCase().replace(/\s+/g, "");
  if (!n) return true;
  let i = 0;
  for (const ch of h) {
    if (ch === n[i]) i++;
    if (i === n.length) return true;
  }
  return false;
}

export function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [records, setRecords] = useState<Item[]>([]);
  const [sel, setSel] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const restoreTo = useRef<HTMLElement | null>(null);

  const close = useCallback(() => {
    setOpen(false);
    setQ("");
    setRecords([]);
    setSel(0);
    restoreTo.current?.focus?.();
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((prev) => {
          if (!prev) restoreTo.current = document.activeElement as HTMLElement | null;
          return !prev;
        });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // Debounced record lookup. `stale` guards against out-of-order responses.
  useEffect(() => {
    const term = q.trim();
    if (!open || term.length < 2) {
      setRecords([]);
      return;
    }
    const tenantId = getTenantId();
    if (!tenantId) return;

    let stale = false;
    const timer = setTimeout(() => {
      Promise.all([
        crm.leads(tenantId, { search: term, limit: 5 }).then((r) => r.leads || []).catch(() => []),
        crmEntities.listContacts(tenantId, { search: term, limit: 5 }).then((r) => r.rows).catch(() => []),
        crmEntities.listCompanies(tenantId, { search: term, limit: 5 }).then((r) => r.rows).catch(() => []),
        crmEntities.listDeals(tenantId, { search: term, limit: 5 }).then((r) => r.rows).catch(() => []),
      ]).then(([leads, contacts, companies, deals]) => {
        if (stale) return;
        setRecords([
          ...leads.map((l) => ({
            key: `lead:${l.assignment_id}`,
            label: l.company_name || l.name || "Unnamed lead",
            hint: ["Lead", l.city, l.tier && `Tier ${l.tier}`].filter(Boolean).join(" · "),
            href: `/leads/${l.assignment_id}`,
          })),
          ...contacts.map((c) => ({
            key: `contact:${c.id}`,
            label: contactName(c),
            hint: ["Contact", c.company_name, c.email].filter(Boolean).join(" · "),
            href: "/contacts",
          })),
          ...companies.map((c) => ({
            key: `company:${c.id}`,
            label: c.name,
            hint: ["Company", c.city, c.domain].filter(Boolean).join(" · "),
            href: "/companies",
          })),
          ...deals.map((d) => ({
            key: `deal:${d.id}`,
            label: d.title,
            hint: ["Deal", d.company_name, d.stage_name].filter(Boolean).join(" · "),
            href: "/deals",
          })),
        ]);
        setSel(0);
      });
    }, 250);

    return () => {
      stale = true;
      clearTimeout(timer);
    };
  }, [q, open]);

  if (!open) return null;

  const navHits = NAV.filter((n) => matches(`${n.label} ${n.href}`, q));
  const flat = [...navHits, ...records];
  const active = flat[Math.min(sel, flat.length - 1)];

  function go(item?: Item) {
    if (!item) return;
    close();
    router.push(item.href);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setSel((s) => (flat.length ? (s + 1) % flat.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSel((s) => (flat.length ? (s - 1 + flat.length) % flat.length : 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      go(active);
    }
  }

  function row(item: Item, index: number) {
    const on = item.key === active?.key;
    return (
      <button aria-label={`Open ${item.label}`}
        key={item.key}
        type="button"
        onMouseEnter={() => setSel(index)}
        onClick={() => go(item)}
        aria-current={on || undefined}
        className={`flex w-full items-baseline gap-2 rounded-lg px-3 py-2 text-left text-sm ${
          on ? "bg-brand-50 text-brand-900 dark:bg-brand-500/15 dark:text-brand-100" : "text-ink"
        }`}
      >
        <span className="truncate font-medium">{item.label}</span>
        {item.hint && <span className="truncate text-xs text-ink-faint">{item.hint}</span>}
      </button>
    );
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center bg-black/40 p-4 pt-[12vh]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="card-pad w-full max-w-lg space-y-2 shadow-lift"
      >
        <input
          ref={inputRef}
          className="input"
          type="text"
          placeholder="Jump to a page, or search leads, contacts, companies, deals…"
          aria-label="Search pages and records"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKeyDown}
        />

        <div className="max-h-[55vh] space-y-2 overflow-y-auto">
          {navHits.length > 0 && (
            <div>
              <p className="px-3 pb-1 text-[11px] uppercase tracking-wide text-ink-faint">Go to</p>
              {navHits.map((n, i) => row(n, i))}
            </div>
          )}
          {records.length > 0 && (
            <div>
              <p className="px-3 pb-1 text-[11px] uppercase tracking-wide text-ink-faint">Records</p>
              {records.map((r, i) => row(r, navHits.length + i))}
            </div>
          )}
          {flat.length === 0 && (
            <p className="px-3 py-6 text-center text-sm text-ink-faint">
              {q.trim().length < 2 ? "Type at least 2 characters to search records." : "No matches."}
            </p>
          )}
        </div>

        <p className="px-1 text-[11px] text-ink-faint">↑↓ move · Enter open · Esc close</p>
      </div>
    </div>
  );
}
