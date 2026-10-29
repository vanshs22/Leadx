"use client";

import { useEffect, useRef, type ReactNode } from "react";
import Link from "next/link";
import { Icon } from "./Icon";

/* -------------------------------------------------------------------------
   Page structure
   ------------------------------------------------------------------------- */

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  meta,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div className="min-w-0">
        {eyebrow ? <div className="eyebrow text-brand-600 dark:text-brand-300">{eyebrow}</div> : null}
        <h1 className={`page-title ${eyebrow ? "mt-1.5" : ""}`}>{title}</h1>
        {description ? <p className="page-sub">{description}</p> : null}
        {meta ? <div className="page-meta">{meta}</div> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

/** Heading row for a section that is not wrapped in a Panel. */
export function SectionHeader({ title, description, actions, id }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; id?: string }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h2 id={id} className="section-title">{title}</h2>
        {description ? <p className="section-sub">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

/** Bordered surface with an optional header row and footer. */
export function Panel({
  title,
  description,
  actions,
  footer,
  children,
  className = "",
  bodyClassName = "panel-body",
  id,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  id?: string;
}) {
  const headingId = id ? `${id}-title` : undefined;
  return (
    <section className={`panel ${className}`} aria-labelledby={title ? headingId : undefined} id={id}>
      {title || actions ? (
        <div className="panel-header">
          <div className="min-w-0">
            {title ? <h2 id={headingId} className="panel-title">{title}</h2> : null}
            {description ? <p className="section-sub">{description}</p> : null}
          </div>
          {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
        </div>
      ) : null}
      <div className={bodyClassName}>{children}</div>
      {footer ? <div className="panel-footer">{footer}</div> : null}
    </section>
  );
}

/* -------------------------------------------------------------------------
   Metrics
   ------------------------------------------------------------------------- */

export type Tone = "neutral" | "brand" | "accent" | "success" | "warn" | "danger";

const TONE_BAR: Record<Tone, string> = {
  neutral: "bg-line-strong",
  brand: "bg-brand-600 dark:bg-brand-300",
  accent: "bg-signal-400",
  success: "bg-emerald-600 dark:bg-emerald-400",
  warn: "bg-amber-500",
  danger: "bg-rust-500",
};

export function StatCard({
  label,
  value,
  hint,
  tone = "neutral",
  icon,
  href,
}: {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  tone?: Tone;
  icon?: ReactNode;
  href?: string;
}) {
  const body = (
    <>
      <span className={`absolute inset-x-0 top-0 h-[2px] rounded-t-xl ${TONE_BAR[tone]}`} aria-hidden="true" />
      <div className="flex items-start justify-between gap-3">
        <div className="stat-label">{label}</div>
        {icon ? <span className="text-ink-faint">{icon}</span> : null}
      </div>
      <div className="stat-value">{value}</div>
      {hint ? <div className="stat-delta">{hint}</div> : null}
    </>
  );
  const cls = "kpi block overflow-hidden";
  return href ? <Link href={href} className={`${cls} card-hover`}>{body}</Link> : <div className={cls}>{body}</div>;
}

export function StatGrid({ children, cols = 4, className = "" }: { children: ReactNode; cols?: 2 | 3 | 4 | 5 | 6; className?: string }) {
  const map = { 2: "sm:grid-cols-2", 3: "sm:grid-cols-2 lg:grid-cols-3", 4: "sm:grid-cols-2 lg:grid-cols-4", 5: "sm:grid-cols-2 lg:grid-cols-5", 6: "sm:grid-cols-3 lg:grid-cols-6" } as const;
  return <div className={`grid gap-3 ${map[cols]} ${className}`}>{children}</div>;
}

/** Definition list for record detail panels. */
export function KeyValue({ items, columns = 2 }: { items: Array<{ label: ReactNode; value: ReactNode }>; columns?: 1 | 2 | 3 }) {
  const map = { 1: "", 2: "sm:grid-cols-2", 3: "sm:grid-cols-3" } as const;
  return (
    <dl className={`grid gap-x-6 gap-y-4 ${map[columns]}`}>
      {items.map((item, index) => (
        <div key={index} className="min-w-0">
          <dt className="label">{item.label}</dt>
          <dd className="mt-1 truncate text-[0.8125rem] font-medium text-ink">{item.value ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

/* -------------------------------------------------------------------------
   Badges
   ------------------------------------------------------------------------- */

export function Badge({ tone = "neutral", children, className = "" }: { tone?: Tone; children: ReactNode; className?: string }) {
  const cls = tone === "warn" ? "badge-warn" : `badge-${tone}`;
  return <span className={`${cls} ${className}`}>{children}</span>;
}

const STATUS_TONE: Record<string, Tone> = {
  active: "success", completed: "success", complete: "success", succeeded: "success", success: "success", healthy: "success", ok: "success", online: "success", won: "success", delivered: "success", verified: "success", approved: "success", connected: "success", paid: "success", sent: "success",
  running: "brand", in_progress: "brand", processing: "brand", queued: "brand", pending: "neutral", scheduled: "brand", claimed: "brand", open: "brand", new: "brand", draft: "neutral",
  partial: "warn", degraded: "warn", warning: "warn", paused: "warn", stale: "warn", retrying: "warn", trial: "warn", review: "warn", needs_review: "warn",
  failed: "danger", error: "danger", cancelled: "neutral", canceled: "neutral", lost: "danger", rejected: "danger", blocked: "danger", suspended: "danger", down: "danger", bounced: "danger", unhealthy: "danger", expired: "danger",
};

export function statusTone(status: unknown): Tone {
  const key = String(status ?? "").toLowerCase().trim().replace(/[\s-]+/g, "_");
  return STATUS_TONE[key] ?? "neutral";
}

/** Status chip with a consistent colour for the same status everywhere. */
export function StatusBadge({ status, label }: { status: unknown; label?: ReactNode }) {
  const tone = statusTone(status);
  const text = label ?? (String(status ?? "unknown").replace(/_/g, " ") || "unknown");
  return (
    <Badge tone={tone} className="capitalize">
      <span className={`h-1.5 w-1.5 rounded-full ${TONE_BAR[tone]}`} aria-hidden="true" />
      {text}
    </Badge>
  );
}

/* -------------------------------------------------------------------------
   Navigation inside a page
   ------------------------------------------------------------------------- */

export function Tabs<T extends string>({ value, onChange, items, label }: { value: T; onChange: (value: T) => void; items: Array<{ value: T; label: ReactNode; count?: number }>; label: string }) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {items.map((item) => {
        const active = item.value === value;
        return (
          <button key={item.value} type="button" role="tab" aria-selected={active} className={active ? "tab-active" : "tab"} onClick={() => onChange(item.value)}>
            {item.label}
            {item.count != null ? <span className="ml-1.5 tabular-nums text-ink-faint">{item.count}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

export function FilterBar({ children, onClear, clearLabel = "Clear filters", className = "" }: { children: ReactNode; onClear?: () => void; clearLabel?: string; className?: string }) {
  return (
    <div className={`filter-bar ${className}`} role="search">
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">{children}</div>
      {onClear ? <button type="button" className="btn-ghost btn-sm shrink-0" onClick={onClear}>{clearLabel}</button> : null}
    </div>
  );
}

export function Pagination({ page, pageCount, onPageChange }: { page: number; pageCount: number; onPageChange: (page: number) => void }) {
  if (pageCount <= 1) return null;
  return (
    <nav className="flex items-center justify-between gap-3 text-xs text-ink-muted" aria-label="Pagination">
      <span className="tabular-nums">Page {page + 1} of {pageCount}</span>
      <div className="flex gap-2">
        <button type="button" className="btn-secondary btn-sm" disabled={page <= 0} onClick={() => onPageChange(Math.max(0, page - 1))}>Previous</button>
        <button type="button" className="btn-secondary btn-sm" disabled={page + 1 >= pageCount} onClick={() => onPageChange(Math.min(pageCount - 1, page + 1))}>Next</button>
      </div>
    </nav>
  );
}

export function DataTable({ caption, children, className = "" }: { caption: string; children: ReactNode; className?: string }) {
  return <div className={`data-table-wrap ${className}`}><table className="data-table"><caption className="sr-only">{caption}</caption>{children}</table></div>;
}

/* -------------------------------------------------------------------------
   Overlays and feedback
   ------------------------------------------------------------------------- */

export function Drawer({ open, title, onClose, children, footer }: { open: boolean; title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = previous; };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="drawer-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <aside className="drawer-panel" role="dialog" aria-modal="true" aria-label={typeof title === "string" ? title : "Details"}>
        <div className="drawer-header"><div className="min-w-0"><div className="eyebrow">Details</div><h2 className="mt-1 truncate text-base font-semibold text-ink">{title}</h2></div><button ref={closeRef} type="button" className="icon-button" aria-label="Close details" onClick={onClose}><Icon name="close" size={16} /></button></div>
        <div className="drawer-body">{children}</div>
        {footer ? <div className="drawer-footer">{footer}</div> : null}
      </aside>
    </div>
  );
}

export function ConfirmDialog({ open, title, description, confirmLabel = "Confirm", onConfirm, onClose, danger = false }: { open: boolean; title: string; description: ReactNode; confirmLabel?: string; onConfirm: () => void; onClose: () => void; danger?: boolean }) {
  if (!open) return null;
  return (
    <div className="dialog-layer" role="presentation">
      <div className="dialog-panel" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-description">
        <div className="eyebrow">Confirmation required</div><h2 id="confirm-title" className="mt-1 text-base font-semibold text-ink">{title}</h2><p id="confirm-description" className="mt-2 text-[0.8125rem] leading-6 text-ink-muted">{description}</p>
        <div className="mt-5 flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button type="button" className={danger ? "btn-danger" : "btn-primary"} onClick={onConfirm}>{confirmLabel}</button></div>
      </div>
    </div>
  );
}

export function Notice({ tone = "neutral", title, children, onDismiss }: { tone?: "neutral" | "success" | "warning" | "danger"; title?: string; children: ReactNode; onDismiss?: () => void }) {
  const icon = tone === "danger" || tone === "warning" ? "alert" : tone === "success" ? "check" : "info";
  return (
    <div className={`notice notice-${tone}`} role={tone === "danger" ? "alert" : "status"}>
      <div className="flex min-w-0 gap-2.5">
        <Icon name={icon} size={16} className="mt-0.5 shrink-0 opacity-80" />
        <div className="min-w-0">{title ? <div className="font-semibold">{title}</div> : null}<div className={title ? "mt-0.5" : ""}>{children}</div></div>
      </div>
      {onDismiss ? <button type="button" className="btn-ghost btn-sm -mr-2 -mt-1 px-2" aria-label="Dismiss notice" onClick={onDismiss}><Icon name="close" size={14} /></button> : null}
    </div>
  );
}

export function QuietBreadcrumbs({ items }: { items: Array<{ label: string; href?: string }> }) {
  return <nav aria-label="Breadcrumb" className="breadcrumbs">{items.map((item, index) => <span key={`${item.label}-${index}`} className="inline-flex items-center gap-1.5">{index > 0 ? <Icon name="chevron" size={12} className="opacity-60" /> : null}{item.href ? <Link href={item.href} className="hover:text-ink">{item.label}</Link> : <span className="text-ink">{item.label}</span>}</span>)}</nav>;
}
