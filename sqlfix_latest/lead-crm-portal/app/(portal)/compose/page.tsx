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
import { PageHeader, Notice } from "@/components/ui/Primitives";

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

function ScoreBadge({ result }: { result: LintResult | null }) {
  if (!result) return null;
  const cls =
    result.verdict === "good"
      ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
      : result.verdict === "fair"
        ? "bg-amber-500/10 text-amber-700 dark:text-amber-400"
        : "bg-rust-500/10 text-rust-600 dark:text-rust-400";
  return <span className={`pill ${cls}`}>{result.score}/100</span>;
}

function IssueList({ result }: { result: LintResult | null }) {
  if (!result) return null;
  const dot: Record<string, string> = {
    error: "bg-rust-500",
    warning: "bg-amber-500",
    info: "bg-signal-500",
    success: "bg-emerald-500",
  };
  return (
    <ul className="mt-2 space-y-1.5">
      {result.issues.map((i, n) => (
        <li key={n} className="flex gap-2 text-xs text-ink-muted">
          <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${dot[i.level] || "bg-ink-faint"}`} />
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

  if (!tenantId) return <div className="page">Choose a workspace first.</div>;

  return (
    <div className="page">
      <PageHeader eyebrow="Copy studio" title="Write an email" description="Start from copy that already gets replies, edit it directly, and check it before it goes out." />

      {error && <Notice tone="danger" title="Composer unavailable">{error}</Notice>}

      <div className="mb-4 flex flex-wrap gap-2">
        {categories.map((c) => (
          <button
            type="button"
            key={c}
            className={category === c ? "pill bg-brand-500/15 text-brand-700 dark:text-brand-300" : "pill"}
            onClick={() => setCategory(c)}
          >
            {CATEGORY_LABELS[c] || c}
          </button>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="space-y-4">
          {/* ---- variant picker ---- */}
          <section className="card-pad">
            <div className="mb-3 flex items-baseline justify-between gap-2">
              <h2 className="font-display text-sm font-semibold text-ink">Angle</h2>
              {recommended && (
                <span className="text-[11px] text-ink-faint">
                  {recommendReason === "exploring"
                    ? "Still gathering data — variants rotate until each has enough sends."
                    : "Recommended by reply rate."}
                </span>
              )}
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              {variants.map((v) => (
                <button
                  key={v.variant_key}
                  onClick={() => apply(v)}
                  className={`rounded-xl2 border p-3 text-left transition ${
                    activeVariant === v.variant_key
                      ? "border-brand-500 bg-brand-500/5"
                      : "border-line hover:bg-surface-3"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-ink">{v.variant_key.replace(/_/g, " ")}</span>
                    {v.variant_key === recommended && <span className="pill text-[10px]">best</span>}
                  </div>
                  <p className="mt-1 text-xs text-ink-muted">{v.angle}</p>
                  <p className="mt-1.5 text-[11px] text-ink-faint">
                    {v.sent > 0
                      ? `${v.sent} sent · ${v.reply_rate ?? 0}% replied${v.confident ? "" : " (early)"}`
                      : "Not sent yet"}
                  </p>
                </button>
              ))}
            </div>
            {variants.find((v) => v.variant_key === activeVariant)?.when && (
              <p className="mt-3 border-t border-line pt-3 text-xs text-ink-muted">
                <span className="font-medium text-ink">When to use: </span>
                {variants.find((v) => v.variant_key === activeVariant)?.when}
              </p>
            )}
          </section>

          {/* ---- editor ---- */}
          <section className="card-pad">
            <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-ink-faint">
              Subject
            </label>
            <div className="flex items-center gap-2">
              <input
                className="input flex-1"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                placeholder="Subject line"
              />
              <ScoreBadge result={subjectLint} />
            </div>
            {subjectOptions.length > 0 && (
              <div className="mt-2 space-y-1 rounded-xl2 border border-line bg-surface-2 p-2">
                <p className="text-[11px] text-ink-faint">Click one to use it:</p>
                {subjectOptions.map((s, i) => (
                  <button
                    key={i}
                    className="block w-full rounded-lg px-2 py-1 text-left text-sm text-ink hover:bg-surface-3"
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

            <div className="mt-4 mb-1 flex items-center justify-between">
              <label className="text-xs font-medium uppercase tracking-wide text-ink-faint">Body</label>
              <div className="flex items-center gap-2">
                <ScoreBadge result={bodyLint} />
                <span className="text-[11px] text-ink-faint">click to edit</span>
              </div>
            </div>
            <div
              ref={editorRef}
              contentEditable
              suppressContentEditableWarning
              onInput={onEditorInput}
              className="min-h-[280px] rounded-xl2 border border-line bg-surface p-4 text-sm leading-relaxed text-ink outline-none focus:border-brand-500"
              aria-label="Email body editor"
            />

            <div className="mt-3 flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] text-ink-faint">Insert:</span>
              {mergeTags.map((t) => (
                <button key={t} className="pill text-[11px]" onClick={() => insertTag(t)}>
                  {`{{${t}}}`}
                </button>
              ))}
            </div>

            <div className="mt-4 flex flex-wrap gap-2 border-t border-line pt-4">
              <button className="btn-primary" onClick={copyAll}>
                {copied ? "Copied" : "Copy subject + body"}
              </button>
              <button
                className="btn-secondary"
                onClick={() => {
                  const v = variants.find((x) => x.variant_key === activeVariant);
                  if (v) apply(v);
                }}
              >
                Reset to template
              </button>
            </div>
          </section>
        </div>

        {/* ---- right rail: rewrite + lint detail ---- */}
        <div className="space-y-4">
          <section className="card-pad">
            <h2 className="mb-1 font-display text-sm font-semibold text-ink">Rewrite</h2>
            <p className="mb-3 text-xs text-ink-muted">
              Keeps your merge tags and facts. Won&apos;t invent numbers or claims.
            </p>
            <div className="mb-3 flex flex-wrap gap-1.5">
              {tones.map((t) => (
                <button
                  key={t}
                  disabled={!!busy}
                  className="pill disabled:opacity-50"
                  onClick={() => rewrite({ tone: t })}
                >
                  {busy === t ? "…" : t}
                </button>
              ))}
            </div>
            <div className="mb-3 flex flex-wrap gap-1.5">
              {modes.map((m) => (
                <button
                  key={m}
                  disabled={!!busy}
                  className="pill bg-signal-500/10 text-signal-700 disabled:opacity-50 dark:text-signal-300"
                  onClick={() => rewrite({ mode: m })}
                >
                  {busy === m ? "…" : m === "subject" ? "subject ideas" : m}
                </button>
              ))}
            </div>
            <input
              className="input mb-2 text-sm"
              placeholder="Or type your own instruction…"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && instruction.trim()) rewrite({ instruction });
              }}
            />
            <button
              className="btn-secondary w-full text-xs"
              disabled={!instruction.trim() || !!busy}
              onClick={() => rewrite({ instruction })}
            >
              {busy === "custom" ? "Rewriting…" : "Apply instruction"}
            </button>
          </section>

          <section className="card-pad">
            <div className="flex items-baseline justify-between">
              <h2 className="font-display text-sm font-semibold text-ink">Subject check</h2>
              {subjectLint?.length != null && (
                <span className="text-[11px] text-ink-faint">{subjectLint.length} chars</span>
              )}
            </div>
            <IssueList result={subjectLint} />
          </section>

          <section className="card-pad">
            <div className="flex items-baseline justify-between">
              <h2 className="font-display text-sm font-semibold text-ink">Body check</h2>
              {bodyLint?.words != null && (
                <span className="text-[11px] text-ink-faint">{bodyLint.words} words</span>
              )}
            </div>
            <IssueList result={bodyLint} />
          </section>
        </div>
      </div>
    </div>
  );
}
