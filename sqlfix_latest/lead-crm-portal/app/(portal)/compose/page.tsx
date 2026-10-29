"use client";

/**
 * Email composer — pick a proven variant, edit it in place, lint it, and let
 * the LLM rewrite it for a tone.
 *
 * The editor is the same click-to-edit contentEditable surface as the docs
 * builder: what you see is what sends, no separate preview to drift out of
 * sync. Lint is rule-based and debounced locally, so it costs nothing to
 * run while you type; the rewrite button is the only thing that hits an LLM.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { crmExtras, type CopyVariant, type LintResult } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { PageHeader, Notice, Panel, Badge } from "@/components/ui/Primitives";
import { EmptyState } from "@/components/ui/States";
import { Icon } from "@/components/ui/Icon";

const CATEGORY_LABELS: Record<string, string> = {
  cold_outreach: "Cold outreach",
  follow_up: "Follow-up",
  proposal: "Proposal",
  invoice: "Invoice",
};

const LINT_DEBOUNCE_MS = 700;

function textToHtml(text: string) {
  return text
    .split(/\n\s*\n/)
    .filter((p) => p.trim())
    .map((p) => `<p>${p.trim().replace(/\n/g, "<br/>")}</p>`)
    .join("");
}

/** Plain-text snippet of the editor HTML for the inbox preview. */
function htmlSnippet(html: string, max = 160) {
  const text = html.replace(/<br\s*\/?>/gi, " ").replace(/<\/p>/gi, " ").replace(/<[^>]+>/g, "").replace(/&nbsp;/g, " ").replace(/\s+/g, " ").trim();
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function ScoreBadge({ result }: { result: LintResult | null }) {
  if (!result) return null;
  const tone = result.verdict === "good" ? "success" : result.verdict === "fair" ? "warn" : "danger";
  return <Badge tone={tone} className="tabular-nums">{result.score}/100</Badge>;
}

function IssueList({ result }: { result: LintResult | null }) {
  if (!result) return <p className="text-xs text-ink-faint">Checks run automatically as you type.</p>;
  const dot: Record<string, string> = {
    error: "bg-rust-500",
    warning: "bg-amber-500",
    info: "bg-brand-500 dark:bg-brand-300",
    success: "bg-emerald-600 dark:bg-emerald-400",
  };
  return (
    <ul className="space-y-2">
      {result.issues.map((i, n) => (
        <li key={n} className="flex gap-2.5 text-xs leading-5 text-ink-muted">
          <span className={`mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full ${dot[i.level] || "bg-ink-faint"}`} aria-hidden="true" />
          <span>{i.message}</span>
        </li>
      ))}
    </ul>
  );
}

export default function ComposePage() {
  const tenantId = getTenantId();
  const editorRef = useRef<HTMLDivElement>(null);

  const [category, setCategory] = useState("cold_outreach");
  const [categories, setCategories] = useState<string[]>(Object.keys(CATEGORY_LABELS));
  const [variants, setVariants] = useState<CopyVariant[]>([]);
  const [recommended, setRecommended] = useState<string | null>(null);
  const [recommendReason, setRecommendReason] = useState<string>("");
  const [activeVariant, setActiveVariant] = useState<string | null>(null);
  const [mergeTags, setMergeTags] = useState<string[]>([]);

  const [subject, setSubject] = useState("");
  const [bodyHtml, setBodyHtml] = useState("");
  const [subjectLint, setSubjectLint] = useState<LintResult | null>(null);
  const [bodyLint, setBodyLint] = useState<LintResult | null>(null);

  const [tones, setTones] = useState<string[]>([]);
  const [modes, setModes] = useState<string[]>([]);
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [subjectOptions, setSubjectOptions] = useState<string[]>([]);
  const [copied, setCopied] = useState(false);

  // Load the variant list (with its live A/B stats) whenever the category
  // changes, and auto-apply whichever variant the backend recommends.
  useEffect(() => {
    if (!tenantId) return;
    let cancelled = false;
    crmExtras
      .copyVariants(tenantId, category)
      .then((r) => {
        if (cancelled) return;
        setVariants(r.variants);
        setMergeTags(r.merge_tags);
        setRecommended(r.recommended?.variant_key ?? null);
        setRecommendReason(r.recommended?.reason ?? "");
        const pick = r.recommended || r.variants[0];
        if (pick) apply(pick as CopyVariant);
      })
      .catch((e) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId, category]);

  useEffect(() => {
    if (!tenantId) return;
    crmExtras
      .emailTones(tenantId)
      .then((r) => {
        setTones(r.tones.map((t) => t.key));
        setModes(r.modes.map((m) => m.key));
      })
      .catch(() => {});
    // Category list comes from the backend so adding a category server-side
    // shows up here without a frontend deploy.
    crmExtras
      .copyCategories(tenantId)
      .then((r) => r.categories.length && setCategories(r.categories))
      .catch(() => {});
  }, [tenantId]);

  // Debounced lint. Cheap enough to run on every pause, so there's no
  // "check my email" button to forget to press.
  useEffect(() => {
    if (!tenantId || (!subject && !bodyHtml)) return;
    const t = setTimeout(() => {
      crmExtras
        .emailLint(tenantId, subject, bodyHtml)
        .then((r) => {
          setSubjectLint(r.subject);
          setBodyLint(r.body);
        })
        .catch(() => {});
    }, LINT_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [tenantId, subject, bodyHtml]);

  const apply = useCallback((v: CopyVariant) => {
    setActiveVariant(v.variant_key);
    setSubject(v.subject);
    const html = textToHtml(v.body_text);
    setBodyHtml(html);
    // Write straight to the DOM: React must not own contentEditable's
    // children or the caret jumps to the start on every keystroke.
    if (editorRef.current) editorRef.current.innerHTML = html;
    setSubjectOptions([]);
  }, []);

  const onEditorInput = () => setBodyHtml(editorRef.current?.innerHTML || "");

  async function rewrite(payload: { tone?: string; mode?: string; instruction?: string }) {
    if (!tenantId) return;
    setBusy(payload.tone || payload.mode || "custom");
    setError(null);
    try {
      const r = await crmExtras.emailRewrite(tenantId, { subject, body_html: bodyHtml, ...payload });
      if (r.subjects?.length) {
        setSubjectOptions(r.subjects);
      } else {
        if (r.subject) setSubject(r.subject);
        if (r.body_html) {
          setBodyHtml(r.body_html);
          if (editorRef.current) editorRef.current.innerHTML = r.body_html;
        }
        if (r.subject_check) setSubjectLint(r.subject_check);
      }
      setInstruction("");
    } catch (e: any) {
      setError(e.message || "Rewrite failed");
    } finally {
      setBusy("");
    }
  }

  function insertTag(tag: string) {
    editorRef.current?.focus();
    document.execCommand("insertText", false, `{{${tag}}}`);
    setBodyHtml(editorRef.current?.innerHTML || "");
  }

  async function copyAll() {
    const plain = (editorRef.current?.innerText || "").trim();
    await navigator.clipboard.writeText(`Subject: ${subject}\n\n${plain}`);
    setCopied(true);
    // Also count it as a send for A/B purposes — copying out is the only
    // signal we get when the email goes out through someone's own inbox.
    if (tenantId && activeVariant) crmExtras.copyTrack(tenantId, category, activeVariant, "sent").catch(() => {});
    setTimeout(() => setCopied(false), 2000);
  }

  if (!tenantId) {
    return (
      <div className="page">
        <PageHeader eyebrow="Copy studio" title="Write an email" />
        <div className="card"><EmptyState title="Choose a workspace first." hint="The composer loads proven copy for the selected workspace." /></div>
      </div>
    );
  }

  const active = variants.find((v) => v.variant_key === activeVariant);
  const snippet = htmlSnippet(bodyHtml);

  return (
    <div className="page">
      <PageHeader
        eyebrow="Copy studio"
        title="Write an email"
        description="Start from copy that already gets replies, edit it directly, and check it before it goes out."
        actions={
          <div className="tabs" role="tablist" aria-label="Email category">
            {categories.map((c) => (
              <button
                type="button"
                role="tab"
                aria-selected={category === c}
                key={c}
                className={category === c ? "tab-active" : "tab"}
                onClick={() => setCategory(c)}
              >
                {CATEGORY_LABELS[c] || c}
              </button>
            ))}
          </div>
        }
      />

      {error && <Notice tone="danger" title="Composer unavailable">{error}</Notice>}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="min-w-0 space-y-4 lg:col-span-2">
          {/* ---- variant picker ---- */}
          <Panel
            title="Angle"
            description={
              recommended
                ? recommendReason === "exploring"
                  ? "Still gathering data — variants rotate until each has enough sends."
                  : "Recommended by reply rate."
                : "Pick the angle that fits this prospect."
            }
          >
            {variants.length === 0 ? (
              <p className="text-[0.8125rem] text-ink-muted">No copy variants loaded for this category yet.</p>
            ) : (
              <div className="grid gap-2 sm:grid-cols-2">
                {variants.map((v) => {
                  const selected = activeVariant === v.variant_key;
                  return (
                    <button
                      key={v.variant_key}
                      type="button"
                      aria-pressed={selected}
                      onClick={() => apply(v)}
                      className={`rounded-lg border p-3 text-left transition-colors ${
                        selected
                          ? "border-brand-500 bg-brand-50 ring-1 ring-brand-500/30 dark:border-brand-300/60 dark:bg-brand-500/10"
                          : "border-line bg-surface-2 hover:border-line-strong hover:bg-surface-3"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[0.8125rem] font-semibold capitalize text-ink">{v.variant_key.replace(/_/g, " ")}</span>
                        {v.variant_key === recommended && <Badge tone="accent">Best</Badge>}
                      </div>
                      <p className="mt-1 text-xs leading-5 text-ink-muted">{v.angle}</p>
                      <p className="mt-2 text-[11px] tabular-nums text-ink-faint">
                        {v.sent > 0
                          ? `${v.sent} sent · ${v.reply_rate ?? 0}% replied${v.confident ? "" : " (early)"}`
                          : "Not sent yet"}
                      </p>
                    </button>
                  );
                })}
              </div>
            )}
            {active?.when && (
              <p className="mt-4 border-t border-line pt-3 text-xs text-ink-muted">
                <span className="font-medium text-ink">When to use: </span>
                {active.when}
              </p>
            )}
          </Panel>

          {/* ---- editor ---- */}
          <Panel
            title="Message"
            actions={<span className="text-[11px] text-ink-faint">What you see is what sends</span>}
            footer={
              <>
                <span>Copying also records a send for A/B results.</span>
                <div className="flex flex-wrap gap-2">
                  <button
                    className="btn-secondary btn-sm"
                    onClick={() => {
                      const v = variants.find((x) => x.variant_key === activeVariant);
                      if (v) apply(v);
                    }}
                  >
                    Reset to template
                  </button>
                  <button className="btn-primary btn-sm" onClick={copyAll}>
                    <Icon name={copied ? "check" : "mail"} size={14} />
                    {copied ? "Copied" : "Copy subject + body"}
                  </button>
                </div>
              </>
            }
          >
            <div className="space-y-5">
              <div>
                <div className="flex items-center justify-between gap-2">
                  <label className="field-label" htmlFor="compose-subject">
                    Subject
                  </label>
                  <ScoreBadge result={subjectLint} />
                </div>
                <input
                  id="compose-subject"
                  className="input"
                  value={subject}
                  onChange={(e) => setSubject(e.target.value)}
                  placeholder="Subject line"
                />
                {subjectOptions.length > 0 && (
                  <div className="mt-2 rounded-lg border border-line bg-surface-3/50 p-2">
                    <p className="px-2 pb-1 text-[11px] text-ink-faint">Click one to use it:</p>
                    {subjectOptions.map((s, i) => (
                      <button
                        key={i}
                        className="block w-full rounded-md px-2 py-1.5 text-left text-[0.8125rem] text-ink hover:bg-surface-2"
                        onClick={() => {
                          setSubject(s);
                          setSubjectOptions([]);
                        }}
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div>
                <div className="flex items-center justify-between gap-2">
                  <label className="field-label" id="compose-body-label">Body</label>
                  <div className="flex items-center gap-2">
                    <ScoreBadge result={bodyLint} />
                    <span className="text-[11px] text-ink-faint">Click to edit</span>
                  </div>
                </div>
                <div
                  ref={editorRef}
                  contentEditable
                  suppressContentEditableWarning
                  onInput={onEditorInput}
                  className="min-h-[300px] rounded-lg border border-line-strong bg-surface-2 px-4 py-3 text-sm leading-7 text-ink shadow-card outline-none focus:border-brand-500 focus:ring-[3px] focus:ring-brand-500/15 dark:focus:border-brand-300 [&_p+p]:mt-3"
                  aria-label="Email body editor"
                  aria-describedby="compose-body-label"
                />
              </div>

              {mergeTags.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="mr-1 text-xs text-ink-faint">Insert:</span>
                  {mergeTags.map((t) => (
                    <button key={t} type="button" aria-label={`Insert merge tag ${t}`} className="rounded-md border border-line bg-surface-3 px-1.5 py-0.5 font-mono text-[11px] text-ink-muted hover:border-line-strong hover:text-ink" onClick={() => insertTag(t)}>
                      {`{{${t}}}`}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </Panel>
        </div>

        {/* ---- right rail: preview, rewrite + lint detail ---- */}
        <div className="min-w-0 space-y-4">
          <Panel title="Inbox preview" description="How the first line reads in a recipient's list.">
            <div className="rounded-lg border border-line bg-surface-3/50 p-3">
              <div className="flex items-center justify-between gap-2 text-[11px] text-ink-faint">
                <span>You</span>
                <span className="tabular-nums">now</span>
              </div>
              <div className="mt-1 truncate text-[0.8125rem] font-semibold text-ink">{subject || "No subject"}</div>
              <p className="mt-0.5 line-clamp-2 text-xs leading-5 text-ink-muted">{snippet || "Body is empty."}</p>
            </div>
          </Panel>

          <Panel title="Rewrite" description="Keeps your merge tags and facts. Won't invent numbers or claims.">
            <div className="space-y-3">
              {tones.length > 0 && (
                <div>
                  <div className="label mb-1.5">Tone</div>
                  <div className="flex flex-wrap gap-1.5">
                    {tones.map((t) => (
                      <button
                        key={t}
                        disabled={!!busy}
                        className="btn-secondary btn-sm capitalize"
                        onClick={() => rewrite({ tone: t })}
                      >
                        {busy === t ? "…" : t}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {modes.length > 0 && (
                <div>
                  <div className="label mb-1.5">Mode</div>
                  <div className="flex flex-wrap gap-1.5">
                    {modes.map((m) => (
                      <button
                        key={m}
                        disabled={!!busy}
                        className="btn-ghost btn-sm border border-line capitalize"
                        onClick={() => rewrite({ mode: m })}
                      >
                        {busy === m ? "…" : m === "subject" ? "subject ideas" : m}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <div>
                <label className="field-label" htmlFor="compose-instruction">Custom instruction</label>
                <input
                  id="compose-instruction"
                  className="input"
                  placeholder="Or type your own instruction…"
                  value={instruction}
                  onChange={(e) => setInstruction(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && instruction.trim()) rewrite({ instruction });
                  }}
                />
                <p className="field-hint">Press Enter or use the button below.</p>
              </div>
              <button
                className="btn-secondary w-full"
                disabled={!instruction.trim() || !!busy}
                onClick={() => rewrite({ instruction })}
              >
                {busy === "custom" ? "Rewriting…" : "Apply instruction"}
              </button>
            </div>
          </Panel>

          <Panel
            title="Subject check"
            actions={subjectLint?.length != null ? <span className="text-[11px] tabular-nums text-ink-faint">{subjectLint.length} chars</span> : undefined}
          >
            <IssueList result={subjectLint} />
          </Panel>

          <Panel
            title="Body check"
            actions={bodyLint?.words != null ? <span className="text-[11px] tabular-nums text-ink-faint">{bodyLint.words} words</span> : undefined}
          >
            <IssueList result={bodyLint} />
          </Panel>
        </div>
      </div>
    </div>
  );
}
