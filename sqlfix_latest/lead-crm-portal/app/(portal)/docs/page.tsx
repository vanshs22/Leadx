"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { crm } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { Badge, PageHeader, Notice, type Tone } from "@/components/ui/Primitives";
import { EmptyState } from "@/components/ui/States";
import { Icon } from "@/components/ui/Icon";

function applyVars(html: string, vars: Record<string, string>) {
  return (html || "").replace(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g, (_, k) =>
    vars[k] != null && String(vars[k]) !== "" ? String(vars[k]) : `{{${k}}}`
  );
}

function parseVarList(raw: unknown): string[] {
  if (!raw) return [];
  if (Array.isArray(raw)) return raw.map(String);
  if (typeof raw === "string") {
    try {
      const j = JSON.parse(raw);
      if (Array.isArray(j)) return j.map(String);
    } catch {
      return raw.split(/[,\s]+/).filter(Boolean);
    }
  }
  if (typeof raw === "object") return Object.keys(raw as object);
  return [];
}

function extractVarsFromHtml(html: string): string[] {
  const keys = new Set<string>();
  const re = /\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(html || ""))) keys.add(m[1]);
  return Array.from(keys);
}

const FIELD_LABELS: Record<string, string> = {
  client_name: "Client name",
  provider_name: "Provider / your company",
  amount: "Amount",
  currency: "Currency",
  scope: "Scope of work",
  term: "Term",
  vertical: "Vertical / industry",
  geo: "Geography",
  lead_count: "Lead count",
  offer_category: "Offer category",
  invoice_number: "Invoice number",
  due_date: "Due date",
  line_items: "Line items",
  party_a: "Party A",
  party_b: "Party B",
};

const DEFAULT_VARS: Record<string, string> = {
  client_name: "Acme Agency",
  provider_name: "LeadX",
  amount: "1,499",
  currency: "USD",
  scope: "Exclusive Tier A local business lead delivery for the agreed ICP and geography.",
  term: "12 months",
  vertical: "medspa",
  geo: "New Jersey",
  lead_count: "200",
  offer_category: "lead_gen_service",
  invoice_number: "INV-001",
  due_date: "Net 15",
  line_items: "Monthly exclusive lead package — Tier A priority delivery",
  party_a: "LeadX",
  party_b: "Acme Agency",
};

const CATEGORY_META: Record<string, { label: string; tone: Tone }> = {
  contract: { label: "Contract", tone: "brand" },
  nda: { label: "NDA", tone: "neutral" },
  invoice: { label: "Invoice", tone: "success" },
  proposal: { label: "Proposal", tone: "accent" },
  onboarding: { label: "Onboarding", tone: "warn" },
};

const FONT_SIZES = ["12px", "14px", "16px", "18px", "20px", "24px", "28px", "32px"];
// Document text colours (written into the user's document HTML, so they must
// be literal values). Drawn from the Ink & Brass palette.
const COLORS: Array<{ value: string; name: string }> = [
  { value: "#111620", name: "Ink" },
  { value: "#485060", name: "Muted ink" },
  { value: "#203B6A", name: "Navy" },
  { value: "#4064A3", name: "Light navy" },
  { value: "#AC8030", name: "Brass" },
  { value: "#1E7A55", name: "Emerald" },
  { value: "#B0761A", name: "Amber" },
  { value: "#B2382E", name: "Rust" },
  { value: "#ffffff", name: "White" },
];
const HIGHLIGHTS: Array<{ value: string; name: string }> = [
  { value: "#F5EBD3", name: "Brass" },
  { value: "#D9EFE4", name: "Emerald" },
  { value: "#E2E8F4", name: "Navy" },
  { value: "#FADFDC", name: "Rust" },
  { value: "#F0F0EC", name: "Porcelain" },
  { value: "transparent", name: "None" },
];

function ToolBtn({
  title,
  onClick,
  active,
  children,
  className = "",
}: {
  title: string;
  onClick: () => void;
  active?: boolean;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      aria-pressed={active}
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      className={`inline-flex h-8 min-w-[32px] items-center justify-center rounded-md px-2 text-xs font-semibold transition ${
        active
          ? "bg-brand-700 text-white dark:bg-brand-300 dark:text-brand-900"
          : "text-ink-muted hover:bg-surface-3 hover:text-ink"
      } ${className}`}
    >
      {children}
    </button>
  );
}

export default function DocsPage() {
  const [templates, setTemplates] = useState<any[]>([]);
  const [selected, setSelected] = useState("");
  const [channel, setChannel] = useState("email");
  const [toEmail, setToEmail] = useState("");
  const [toPhone, setToPhone] = useState("");
  const [filter, setFilter] = useState("all");
  const [vars, setVars] = useState<Record<string, string>>({ ...DEFAULT_VARS });
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const editorRef = useRef<HTMLDivElement>(null);
  const skipNextSync = useRef(false);

  useEffect(() => {
    crm
      .docsTemplates(getTenantId())
      .then((r) => {
        const list = r.templates || [];
        setTemplates(list);
        if (list.length) setSelected((prev) => prev || list[0].id);
      })
      .catch((e) => setMsg(e.message));
  }, []);

  const selectedTpl = templates.find((t) => t.id === selected);

  const fieldKeys = useMemo(() => {
    if (!selectedTpl) return [] as string[];
    const fromMeta = parseVarList(selectedTpl.variables);
    const fromHtml = extractVarsFromHtml(selectedTpl.body_html || "");
    const fromText = extractVarsFromHtml(selectedTpl.body_text || "");
    const merged = [...fromMeta];
    for (const k of [...fromHtml, ...fromText]) {
      if (!merged.includes(k)) merged.push(k);
    }
    return merged.length ? merged : Object.keys(DEFAULT_VARS);
  }, [selectedTpl]);

  const filtered = useMemo(() => {
    if (filter === "all") return templates;
    return templates.filter((t) => (t.category || "") === filter);
  }, [templates, filter]);

  const renderedHtml = useMemo(() => {
    if (!selectedTpl?.body_html) {
      return `<div style="padding:48px;text-align:center;font-family:system-ui;color:#767D8C">Select a template</div>`;
    }
    return applyVars(selectedTpl.body_html, vars);
  }, [selectedTpl, vars]);

  useEffect(() => {
    if (dirty) return;
    const el = editorRef.current;
    if (!el) return;
    skipNextSync.current = true;
    el.innerHTML = renderedHtml;
  }, [renderedHtml, dirty, selected]);

  const selectTemplate = useCallback(
    (id: string) => {
      setSelected(id);
      setDirty(false);
      setMsg("");
      const tpl = templates.find((t) => t.id === id);
      if (!tpl) return;
      const keys = (() => {
        const fromMeta = parseVarList(tpl.variables);
        const fromHtml = extractVarsFromHtml(tpl.body_html || "");
        const merged = [...fromMeta];
        for (const k of fromHtml) if (!merged.includes(k)) merged.push(k);
        return merged;
      })();
      setVars((prev) => {
        const next = { ...prev };
        for (const k of keys) {
          if (next[k] == null || next[k] === "") next[k] = DEFAULT_VARS[k] || "";
        }
        return next;
      });
    },
    [templates]
  );

  function onFieldChange(key: string, value: string) {
    setVars((prev) => ({ ...prev, [key]: value }));
    setDirty(false);
  }

  function onEditorInput() {
    if (skipNextSync.current) {
      skipNextSync.current = false;
      return;
    }
    setDirty(true);
  }

  function resetPreview() {
    setDirty(false);
    setMsg("Preview reset to template + fields");
  }

  function focusEditor() {
    editorRef.current?.focus();
  }

  function runCmd(cmd: string, value?: string) {
    focusEditor();
    try {
      document.execCommand(cmd, false, value);
    } catch {
      /* ignore */
    }
    setDirty(true);
  }

  function insertBox() {
    focusEditor();
    const html =
      '<div style="margin:12px 0;padding:14px 16px;border:1px solid #C4D1E8;border-left:4px solid #203B6A;border-radius:8px;background:#F1F4FA;color:#182D51;">Highlight box — type here</div><p><br/></p>';
    try {
      document.execCommand("insertHTML", false, html);
    } catch {
      /* ignore */
    }
    setDirty(true);
  }

  function insertDivider() {
    focusEditor();
    try {
      document.execCommand(
        "insertHTML",
        false,
        '<hr style="border:none;border-top:1px solid #E3E3DD;margin:20px 0;"/><p><br/></p>'
      );
    } catch {
      /* ignore */
    }
    setDirty(true);
  }

  function getEditorHtml(): string {
    return editorRef.current?.innerHTML || renderedHtml;
  }

  function downloadDoc() {
    const name = (selectedTpl?.name || "document").replace(/[^\w\-]+/g, "_");
    const body = getEditorHtml();
    const full = `<!DOCTYPE html><html><head><meta charset="utf-8"/><title>${name}</title>
<style>body{margin:0;background:#F6F6F3;font-family:Inter,Segoe UI,system-ui,sans-serif}
.wrap{max-width:800px;margin:24px auto;background:#fff;padding:24px;border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,.08)}</style>
</head><body><div class="wrap">${body}</div></body></html>`;
    const blob = new Blob([full], { type: "text/html;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${name}.html`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    setMsg(`Downloaded ${name}.html — open in browser → Print → Save as PDF if needed`);
  }

  async function send() {
    if (!selected) return;
    setBusy(true);
    try {
      const r: any = await crm.docsSend(getTenantId(), {
        template_id: selected,
        variables: vars,
        channel,
        to_email: toEmail || undefined,
        to_phone: toPhone || undefined,
      });
      setMsg(
        r.ok
          ? dirty
            ? `Sent via ${channel} (server uses template + fields; download keeps your on-screen edits)`
            : `Sent via ${channel}`
          : r.error || "Failed"
      );
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  }

  const categories = ["all", ...Array.from(new Set(templates.map((t) => t.category).filter(Boolean)))];
  const meta = CATEGORY_META[selectedTpl?.category] || {
    label: selectedTpl?.category || "Doc",
    tone: "neutral" as Tone,
  };
  const divider = <div className="mx-1 h-5 w-px bg-line" aria-hidden="true" />;

  return (
    <div className="page">
      <PageHeader
        eyebrow="Workspace documents"
        title="Document editor"
        description="Template · fields · format like Word · download or send"
        actions={
          <>
            {dirty && <button type="button" onClick={resetPreview} className="btn-ghost">Reset preview</button>}
            <button type="button" onClick={downloadDoc} className="btn-secondary" disabled={!selectedTpl}><Icon name="download" size={14} />Download</button>
            <select aria-label="Delivery channel" className="select w-auto min-w-[120px]" value={channel} onChange={(e) => setChannel(e.target.value)}><option value="email">Email</option><option value="whatsapp">WhatsApp</option></select>
            <button type="button" disabled={busy || !selected} onClick={send} className="btn-primary"><Icon name="mail" size={14} />{busy ? "Sending…" : "Generate & send"}</button>
          </>
        }
        meta={selectedTpl ? <span className="inline-flex items-center gap-2"><Badge tone={meta.tone}>{meta.label}</Badge><span>{selectedTpl.name}</span><span aria-hidden="true">·</span><span className={dirty ? "font-medium text-amber-700 dark:text-amber-300" : ""}>{dirty ? "Editing document" : "Live from fields"}</span></span> : <span className="tabular-nums">{templates.length} templates</span>}
      />

      {msg && <Notice tone={msg.toLowerCase().includes("failed") || msg.toLowerCase().includes("error") ? "danger" : "neutral"}>{msg}</Notice>}

      <div className="grid items-start gap-6 lg:grid-cols-[240px_minmax(0,1fr)] xl:grid-cols-[240px_minmax(0,1fr)_300px]">
        {/* In-page navigation: template library */}
        <nav aria-label="Document templates" className="min-w-0 lg:sticky lg:top-6">
          <div className="label mb-2 hidden lg:block">Template library</div>
          <div className="mb-3 flex flex-wrap gap-1" role="group" aria-label="Filter templates by category">
            {categories.map((c) => (
              <button
                key={c}
                type="button"
                aria-pressed={filter === c}
                onClick={() => setFilter(c)}
                className={filter === c ? "tab-active" : "tab"}
              >
                {c === "all" ? "All" : CATEGORY_META[c]?.label || c}
              </button>
            ))}
          </div>
          <ul className="-mx-1 flex gap-1 overflow-x-auto px-1 pb-1 lg:mx-0 lg:max-h-[calc(100vh-14rem)] lg:flex-col lg:overflow-y-auto lg:overflow-x-visible lg:px-0">
            {filtered.map((t) => {
              const active = t.id === selected;
              const cm = CATEGORY_META[t.category] || { label: t.category };
              return (
                <li key={t.id} className="shrink-0 lg:shrink">
                  <button
                    type="button"
                    aria-current={active ? "page" : undefined}
                    onClick={() => selectTemplate(t.id)}
                    className={`w-[200px] rounded-lg border-l-2 px-3 py-2 text-left transition lg:w-full ${
                      active
                        ? "border-l-brand-600 bg-brand-50 dark:border-l-brand-300 dark:bg-brand-500/10"
                        : "border-l-transparent hover:bg-surface-3"
                    }`}
                  >
                    <div className={`text-xs ${active ? "text-brand-700 dark:text-brand-300" : "text-ink-faint"}`}>{cm.label}</div>
                    <div className={`mt-0.5 line-clamp-2 text-[0.8125rem] ${active ? "font-semibold text-ink" : "font-medium text-ink-muted"}`}>{t.name}</div>
                  </button>
                </li>
              );
            })}
          </ul>
          {!filtered.length && (
            <div className="panel">
              <EmptyState title="No templates" hint="Run professional templates SQL in Supabase." icon={<Icon name="job" size={18} />} />
            </div>
          )}
        </nav>

        {/* Reading / editing column */}
        <section className="min-w-0 space-y-3" aria-label="Document">
          <div className="sticky top-0 z-10 flex flex-wrap items-center gap-1 rounded-lg border border-line bg-surface-2 px-2 py-1.5 shadow-card" role="toolbar" aria-label="Formatting">
            <ToolBtn title="Bold" onClick={() => runCmd("bold")}>
              <span className="font-bold">B</span>
            </ToolBtn>
            <ToolBtn title="Italic" onClick={() => runCmd("italic")}>
              <span className="italic">I</span>
            </ToolBtn>
            <ToolBtn title="Underline" onClick={() => runCmd("underline")}>
              <span className="underline">U</span>
            </ToolBtn>
            <ToolBtn title="Strikethrough" onClick={() => runCmd("strikeThrough")}>
              <span className="line-through">S</span>
            </ToolBtn>
            {divider}
            <ToolBtn title="Bullet list" onClick={() => runCmd("insertUnorderedList")}>
              • List
            </ToolBtn>
            <ToolBtn title="Numbered list" onClick={() => runCmd("insertOrderedList")}>
              1. List
            </ToolBtn>
            {divider}
            <ToolBtn title="Align left" onClick={() => runCmd("justifyLeft")}>
              Left
            </ToolBtn>
            <ToolBtn title="Align center" onClick={() => runCmd("justifyCenter")}>
              Center
            </ToolBtn>
            <ToolBtn title="Align right" onClick={() => runCmd("justifyRight")}>
              Right
            </ToolBtn>
            {divider}
            <label htmlFor="docs-font-size" className="px-1 text-xs text-ink-faint">Size</label>
            <select
              id="docs-font-size"
              className="h-8 rounded-md border border-line bg-surface-2 px-1 text-xs tabular-nums text-ink"
              defaultValue="16px"
              onMouseDown={(e) => e.stopPropagation()}
              onChange={(e) => {
                runCmd("fontSize", "3");
                runCmd("fontSize", e.target.value);
              }}
              onInput={(e) => {
                const v = (e.target as HTMLSelectElement).value;
                focusEditor();
                document.execCommand("styleWithCSS", false, "true");
                // fontSize cmd uses 1-7; apply CSS via insert if needed
                const sel = window.getSelection();
                if (sel && sel.rangeCount) {
                  try {
                    document.execCommand("fontSize", false, "4");
                    const fonts = editorRef.current?.querySelectorAll('font[size="4"]');
                    fonts?.forEach((f) => {
                      const span = document.createElement("span");
                      span.style.fontSize = v;
                      span.innerHTML = f.innerHTML;
                      f.replaceWith(span);
                    });
                  } catch {
                    /* ignore */
                  }
                }
                setDirty(true);
              }}
            >
              {FONT_SIZES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            {divider}
            <span className="px-1 text-xs text-ink-faint" id="docs-color-label">Color</span>
            <div className="flex items-center gap-1" role="group" aria-labelledby="docs-color-label">
              {COLORS.map((c) => (
                <button
                  key={c.value}
                  type="button"
                  title={c.name}
                  aria-label={`Text colour ${c.name}`}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => {
                    focusEditor();
                    document.execCommand("styleWithCSS", false, "true");
                    document.execCommand("foreColor", false, c.value);
                    setDirty(true);
                  }}
                  className="h-5 w-5 rounded border border-line-strong"
                  style={{ background: c.value }}
                />
              ))}
            </div>
            {divider}
            <span className="px-1 text-xs text-ink-faint" id="docs-highlight-label">Highlight</span>
            <div className="flex items-center gap-1" role="group" aria-labelledby="docs-highlight-label">
              {HIGHLIGHTS.map((c) => (
                <button
                  key={c.value}
                  type="button"
                  title={c.name}
                  aria-label={`Highlight ${c.name}`}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => {
                    focusEditor();
                    document.execCommand("styleWithCSS", false, "true");
                    document.execCommand("hiliteColor", false, c.value === "transparent" ? "#ffffff" : c.value);
                    setDirty(true);
                  }}
                  className={`flex h-5 w-5 items-center justify-center rounded border border-line-strong ${c.value === "transparent" ? "bg-surface-2 text-ink-faint" : ""}`}
                  style={c.value === "transparent" ? undefined : { background: c.value }}
                >
                  {c.value === "transparent" ? <Icon name="close" size={12} /> : null}
                </button>
              ))}
            </div>
            {divider}
            <ToolBtn title="Callout box" onClick={insertBox} className="text-brand-700 dark:text-brand-300">
              Box
            </ToolBtn>
            <ToolBtn title="Divider line" onClick={insertDivider}>
              —
            </ToolBtn>
            <ToolBtn title="Clear formatting" onClick={() => runCmd("removeFormat")}>
              Clear
            </ToolBtn>
            <ToolBtn title="Undo" onClick={() => runCmd("undo")}>
              Undo
            </ToolBtn>
            <ToolBtn title="Redo" onClick={() => runCmd("redo")}>
              Redo
            </ToolBtn>
          </div>

          {/* The paper stays white in both themes: it is the printed artefact. */}
          <div className="rounded-xl border border-line bg-surface-3 p-3 sm:p-6">
            <div className="mx-auto max-w-[720px] overflow-hidden rounded-lg bg-white shadow-lift ring-1 ring-line" style={{ minHeight: 560 }}>
              <div
                ref={editorRef}
                contentEditable
                suppressContentEditableWarning
                onInput={onEditorInput}
                className="prose prose-sm min-h-[560px] max-h-[min(78vh,900px)] max-w-none overflow-y-auto p-6 text-left text-[0.9375rem] leading-7 text-brand-900 caret-brand-600 outline-none sm:p-10"
                aria-label="Document editor"
              />
            </div>
          </div>
          {dirty && (
            <p className="px-1 text-xs leading-5 text-ink-faint">
              You edited the document (format tools or typing). <strong className="text-ink-muted">Download</strong> saves exactly what you see.
              <strong className="text-ink-muted"> Send</strong> still uses template + field values (backend path unchanged). Use Reset to re-sync fields.
            </p>
          )}
        </section>

        {/* Fields and delivery */}
        <aside className="min-w-0 space-y-4 lg:col-start-2 xl:col-start-auto xl:sticky xl:top-6" aria-label="Document fields">
          <section className="panel" aria-labelledby="docs-fields-title">
            <div className="panel-header">
              <h2 id="docs-fields-title" className="panel-title">Fields for this document</h2>
              <span className="text-xs tabular-nums text-ink-faint">{fieldKeys.length}</span>
            </div>
            <div className="max-h-[min(60vh,560px)] space-y-4 overflow-y-auto p-5">
              {fieldKeys.map((k) => (
                <label key={k} className="block">
                  <span className="field-label">{FIELD_LABELS[k] || k.replace(/_/g, " ")}</span>
                  {k === "scope" || k === "line_items" ? (
                    <textarea
                      className="textarea min-h-[72px] resize-y"
                      value={vars[k] ?? ""}
                      onChange={(e) => onFieldChange(k, e.target.value)}
                    />
                  ) : (
                    <input className="input" value={vars[k] ?? ""} onChange={(e) => onFieldChange(k, e.target.value)} />
                  )}
                  <code className="mt-1 block font-mono text-xs text-ink-faint">{`{{${k}}}`}</code>
                </label>
              ))}
            </div>
          </section>
          <section className="panel" aria-labelledby="docs-send-title">
            <div className="panel-header"><h2 id="docs-send-title" className="panel-title">Send to</h2></div>
            <div className="space-y-3 p-5">
              <label className="block"><span className="field-label">Email</span><input className="input" type="email" placeholder="client@email.com" value={toEmail} onChange={(e) => setToEmail(e.target.value)} /></label>
              <label className="block"><span className="field-label">WhatsApp</span><input className="input" placeholder="WhatsApp +1…" value={toPhone} onChange={(e) => setToPhone(e.target.value)} /></label>
              <button type="button" onClick={downloadDoc} className="btn-secondary w-full" disabled={!selectedTpl}>
                <Icon name="download" size={14} />
                Download HTML
              </button>
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}
