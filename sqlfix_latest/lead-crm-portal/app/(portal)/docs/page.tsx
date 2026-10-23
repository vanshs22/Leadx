"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { crm } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { PageHeader, Notice } from "@/components/ui/Primitives";

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

const CATEGORY_META: Record<string, { label: string; color: string }> = {
  contract: { label: "Contract", color: "bg-brand-500/15 text-brand-600 dark:text-brand-300" },
  nda: { label: "NDA", color: "bg-sky-500/15 text-sky-600 dark:text-sky-300" },
  invoice: { label: "Invoice", color: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-300" },
  proposal: { label: "Proposal", color: "bg-violet-500/15 text-violet-600 dark:text-violet-300" },
  onboarding: { label: "Onboarding", color: "bg-amber-500/15 text-amber-700 dark:text-amber-300" },
};

const FONT_SIZES = ["12px", "14px", "16px", "18px", "20px", "24px", "28px", "32px"];
const COLORS = [
  "#0f172a",
  "#334155",
  "#4f46e5",
  "#7c3aed",
  "#0ea5e9",
  "#059669",
  "#dc2626",
  "#ea580c",
  "#ffffff",
];
const HIGHLIGHTS = ["#fef08a", "#bbf7d0", "#bfdbfe", "#fbcfe8", "#e2e8f0", "transparent"];

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
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      className={`h-8 min-w-[32px] px-2 rounded-md text-xs font-semibold border transition ${
        active
          ? "bg-brand-600 text-white border-brand-600"
          : "bg-surface border-line text-ink hover:bg-surface-2"
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
      return `<div style="padding:48px;text-align:center;font-family:system-ui;color:#94a3b8">Select a template</div>`;
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
      '<div style="margin:12px 0;padding:14px 16px;border:1px solid #c7d2fe;border-left:4px solid #4f46e5;border-radius:10px;background:#eef2ff;color:#312e81;">Highlight box — type here</div><p><br/></p>';
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
        '<hr style="border:none;border-top:1px solid #e2e8f0;margin:20px 0;"/><p><br/></p>'
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
<style>body{margin:0;background:#e8eef5;font-family:Inter,Segoe UI,system-ui,sans-serif}
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
    color: "bg-surface-2 text-ink-muted",
  };

  return (
    <div className="page max-w-[1400px] space-y-4">
      <PageHeader eyebrow="Workspace documents" title="Document editor" description="Template · fields · format like Word · download or send" actions={<div className="flex flex-wrap items-center gap-2">{dirty && <button type="button" onClick={resetPreview} className="btn-ghost text-xs">Reset preview</button>}<button type="button" onClick={downloadDoc} className="btn-secondary text-sm" disabled={!selectedTpl}>Download</button><select aria-label="Delivery channel" className="input max-w-[120px] text-sm" value={channel} onChange={(e) => setChannel(e.target.value)}><option value="email">Email</option><option value="whatsapp">WhatsApp</option></select><button disabled={busy || !selected} onClick={send} className="btn-primary">{busy ? "Sending…" : "Generate & send"}</button></div>} />

      {msg && <Notice tone={msg.toLowerCase().includes("failed") || msg.toLowerCase().includes("error") ? "danger" : "neutral"}>{msg}</Notice>}

      <div className="space-y-2">
        <div className="flex flex-wrap gap-2">
          {categories.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => setFilter(c)}
              className={`rounded-full px-3 py-1 text-xs font-semibold transition ${
                filter === c ? "bg-brand-600 text-white" : "bg-surface-2 text-ink-muted border border-line hover:text-ink"
              }`}
            >
              {c === "all" ? "All" : CATEGORY_META[c]?.label || c}
            </button>
          ))}
        </div>
        <div className="flex gap-2 overflow-x-auto pb-1">
          {filtered.map((t) => {
            const active = t.id === selected;
            const cm = CATEGORY_META[t.category] || { label: t.category };
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => selectTemplate(t.id)}
                className={`shrink-0 text-left rounded-xl border px-3.5 py-2.5 min-w-[160px] max-w-[200px] transition ${
                  active
                    ? "border-brand-500 ring-2 ring-brand-500/25 bg-brand-500/5"
                    : "border-line bg-surface hover:bg-surface-2"
                }`}
              >
                <div className={`text-[10px] font-bold uppercase tracking-wide ${active ? "text-brand-500" : "text-ink-faint"}`}>
                  {cm.label}
                </div>
                <div className="text-sm font-semibold text-ink mt-0.5 line-clamp-2">{t.name}</div>
              </button>
            );
          })}
          {!filtered.length && (
            <div className="text-sm text-ink-faint py-3">No templates. Run professional templates SQL in Supabase.</div>
          )}
        </div>
      </div>

      {selectedTpl && (
        <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-line bg-surface px-4 py-3">
          <span className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full ${meta.color}`}>{meta.label}</span>
          <h2 className="text-base font-semibold text-ink flex-1 min-w-0 truncate">{selectedTpl.name}</h2>
          {dirty ? (
            <span className="text-[11px] font-medium text-amber-600 dark:text-amber-400">Editing document</span>
          ) : (
            <span className="text-[11px] text-ink-faint">Live from fields</span>
          )}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-12 items-start">
        <aside className="lg:col-span-4 xl:col-span-3 card-pad space-y-3 lg:sticky lg:top-4">
          <div className="text-xs font-bold uppercase tracking-wider text-ink-faint">Fields for this document</div>
          <div className="space-y-3 max-h-[min(70vh,640px)] overflow-y-auto pr-1">
            {fieldKeys.map((k) => (
              <label key={k} className="block text-xs font-medium text-ink-muted">
                {FIELD_LABELS[k] || k.replace(/_/g, " ")}
                {k === "scope" || k === "line_items" ? (
                  <textarea
                    className="input mt-1 min-h-[72px] resize-y"
                    value={vars[k] ?? ""}
                    onChange={(e) => onFieldChange(k, e.target.value)}
                  />
                ) : (
                  <input className="input mt-1" value={vars[k] ?? ""} onChange={(e) => onFieldChange(k, e.target.value)} />
                )}
              </label>
            ))}
          </div>
          <div className="pt-3 border-t border-line space-y-2">
            <div className="text-xs font-bold uppercase tracking-wider text-ink-faint">Send to</div>
            <input className="input" placeholder="client@email.com" value={toEmail} onChange={(e) => setToEmail(e.target.value)} />
            <input className="input" placeholder="WhatsApp +1…" value={toPhone} onChange={(e) => setToPhone(e.target.value)} />
            <button type="button" onClick={downloadDoc} className="btn-secondary w-full text-sm" disabled={!selectedTpl}>
              Download HTML
            </button>
          </div>
        </aside>

        <section className="lg:col-span-8 xl:col-span-9 space-y-2 min-w-0">
          {/* Word-like toolbar */}
          <div className="flex flex-wrap items-center gap-1.5 rounded-xl border border-line bg-surface px-2 py-2 sticky top-0 z-10 shadow-sm">
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
            <div className="w-px h-6 bg-line mx-0.5" />
            <ToolBtn title="Bullet list" onClick={() => runCmd("insertUnorderedList")}>
              • List
            </ToolBtn>
            <ToolBtn title="Numbered list" onClick={() => runCmd("insertOrderedList")}>
              1. List
            </ToolBtn>
            <div className="w-px h-6 bg-line mx-0.5" />
            <ToolBtn title="Align left" onClick={() => runCmd("justifyLeft")}>
              Left
            </ToolBtn>
            <ToolBtn title="Align center" onClick={() => runCmd("justifyCenter")}>
              Center
            </ToolBtn>
            <ToolBtn title="Align right" onClick={() => runCmd("justifyRight")}>
              Right
            </ToolBtn>
            <div className="w-px h-6 bg-line mx-0.5" />
            <label className="text-[10px] text-ink-faint px-1">Size</label>
            <select
              className="h-8 rounded-md border border-line bg-surface text-xs px-1"
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
            <div className="w-px h-6 bg-line mx-0.5" />
            <span className="text-[10px] text-ink-faint px-1">Color</span>
            {COLORS.map((c) => (
              <button
                key={c}
                type="button"
                title={c}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  focusEditor();
                  document.execCommand("styleWithCSS", false, "true");
                  document.execCommand("foreColor", false, c);
                  setDirty(true);
                }}
                className="h-6 w-6 rounded border border-line shadow-sm"
                style={{ background: c === "#ffffff" ? "#fff" : c }}
              />
            ))}
            <div className="w-px h-6 bg-line mx-0.5" />
            <span className="text-[10px] text-ink-faint px-1">Highlight</span>
            {HIGHLIGHTS.map((c) => (
              <button
                key={c}
                type="button"
                title={c}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  focusEditor();
                  document.execCommand("styleWithCSS", false, "true");
                  document.execCommand("hiliteColor", false, c === "transparent" ? "#ffffff" : c);
                  setDirty(true);
                }}
                className="h-6 w-6 rounded border border-line"
                style={{ background: c === "transparent" ? "repeating-linear-gradient(45deg,#ccc 0 2px,#fff 2px 4px)" : c }}
              />
            ))}
            <div className="w-px h-6 bg-line mx-0.5" />
            <ToolBtn title="Callout box" onClick={insertBox} className="text-brand-600 dark:text-brand-300">
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

          <div className="rounded-2xl border border-line bg-slate-200/70 dark:bg-slate-950/50 p-3 sm:p-6 shadow-inner">
            <div className="mx-auto max-w-[720px] rounded-xl bg-white shadow-2xl ring-1 ring-black/10 overflow-hidden" style={{ minHeight: 560 }}>
              <div
                ref={editorRef}
                contentEditable
                suppressContentEditableWarning
                onInput={onEditorInput}
                className="outline-none min-h-[560px] max-h-[min(78vh,900px)] overflow-y-auto text-left text-slate-900 prose prose-sm max-w-none p-1"
                style={{ caretColor: "#4f46e5" }}
                aria-label="Document editor"
              />
            </div>
          </div>
          {dirty && (
            <p className="text-[11px] text-ink-faint px-1">
              You edited the document (format tools or typing). <strong>Download</strong> saves exactly what you see.
              <strong> Send</strong> still uses template + field values (backend path unchanged). Use Reset to re-sync fields.
            </p>
          )}
        </section>
      </div>
    </div>
  );
}
