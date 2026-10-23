"use client";

import { useEffect, useRef, type ReactNode } from "react";
import Link from "next/link";
import { Icon } from "./Icon";

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
        {eyebrow ? <div className="eyebrow text-brand-700 dark:text-brand-300">{eyebrow}</div> : null}
        <h1 className="page-title mt-1">{title}</h1>
        {description ? <p className="page-sub">{description}</p> : null}
        {meta ? <div className="page-meta">{meta}</div> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

export function FilterBar({ children, onClear, clearLabel = "Clear filters", className = "" }: { children: ReactNode; onClear?: () => void; clearLabel?: string; className?: string }) {
  return (
    <div className={`filter-bar ${className}`} role="search">
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">{children}</div>
      {onClear ? <button type="button" className="btn-ghost shrink-0 text-xs" onClick={onClear}>{clearLabel}</button> : null}
    </div>
  );
}

export function Pagination({ page, pageCount, onPageChange }: { page: number; pageCount: number; onPageChange: (page: number) => void }) {
  if (pageCount <= 1) return null;
  return (
    <nav className="flex items-center justify-between gap-3 text-sm text-ink-muted" aria-label="Pagination">
      <span>Page {page + 1} of {pageCount}</span>
      <div className="flex gap-2">
        <button type="button" className="btn-secondary" disabled={page <= 0} onClick={() => onPageChange(Math.max(0, page - 1))}>Previous</button>
        <button type="button" className="btn-secondary" disabled={page + 1 >= pageCount} onClick={() => onPageChange(Math.min(pageCount - 1, page + 1))}>Next</button>
      </div>
    </nav>
  );
}

export function DataTable({ caption, children, className = "" }: { caption: string; children: ReactNode; className?: string }) {
  return <div className={`data-table-wrap ${className}`}><table className="data-table"><caption className="sr-only">{caption}</caption>{children}</table></div>;
}

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
        <div className="drawer-header"><div className="min-w-0"><div className="eyebrow">Workspace detail</div><h2 className="mt-1 truncate text-lg font-semibold text-ink">{title}</h2></div><button ref={closeRef} type="button" className="icon-button" aria-label="Close details" onClick={onClose}><Icon name="arrow" size={16} className="rotate-180" /></button></div>
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
        <div className="eyebrow">Confirmation required</div><h2 id="confirm-title" className="mt-1 text-lg font-semibold text-ink">{title}</h2><p id="confirm-description" className="mt-2 text-sm leading-6 text-ink-muted">{description}</p>
        <div className="mt-5 flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button type="button" className={danger ? "btn-danger" : "btn-primary"} onClick={onConfirm}>{confirmLabel}</button></div>
      </div>
    </div>
  );
}

export function Notice({ tone = "neutral", title, children, onDismiss }: { tone?: "neutral" | "success" | "warning" | "danger"; title?: string; children: ReactNode; onDismiss?: () => void }) {
  return <div className={`notice notice-${tone}`} role={tone === "danger" ? "alert" : "status"}><div className="min-w-0">{title ? <div className="font-semibold">{title}</div> : null}<div className={title ? "mt-0.5" : ""}>{children}</div></div>{onDismiss ? <button type="button" className="btn-ghost -mr-2 -mt-1 px-2" aria-label="Dismiss notice" onClick={onDismiss}>×</button> : null}</div>;
}

export function QuietBreadcrumbs({ items }: { items: Array<{ label: string; href?: string }> }) {
  return <nav aria-label="Breadcrumb" className="breadcrumbs">{items.map((item, index) => <span key={`${item.label}-${index}`} className="inline-flex items-center gap-2">{index > 0 ? <span aria-hidden="true">/</span> : null}{item.href ? <Link href={item.href} className="hover:text-ink">{item.label}</Link> : <span className="text-ink">{item.label}</span>}</span>)}</nav>;
}
