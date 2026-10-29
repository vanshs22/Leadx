"use client";

/*
 * global-error replaces the root layout, so globals.css and the theme tokens are
 * not guaranteed to be loaded. Styles are inline on purpose and mirror the
 * "Ink & Brass" dark palette (surface, ink, navy primary, brass dot).
 */
const palette = {
  canvas: "rgb(10 15 25)",
  card: "rgb(16 23 36)",
  line: "rgb(35 46 66)",
  ink: "rgb(233 237 244)",
  muted: "rgb(165 175 193)",
  faint: "rgb(120 131 151)",
  navy: "rgb(27 45 80)",
  brass: "rgb(219 184 111)",
  primary: "rgb(196 209 232)",
  primaryInk: "rgb(17 33 60)",
};

export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en">
      <body style={{ margin: 0, fontFamily: '"Inter Variable", Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif', background: palette.canvas, color: palette.ink, WebkitFontSmoothing: "antialiased" }}>
        <main style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 24, boxSizing: "border-box" }}>
          <section role="alert" style={{ maxWidth: 480, width: "100%", boxSizing: "border-box", padding: 32, border: `1px solid ${palette.line}`, borderRadius: 12, background: palette.card, boxShadow: "0 12px 32px -8px rgb(0 0 0 / .45)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span aria-hidden="true" style={{ position: "relative", display: "inline-flex", width: 36, height: 36, alignItems: "center", justifyContent: "center", borderRadius: 8, background: palette.navy, color: "white", fontSize: 11, fontWeight: 700 }}>
                LX<span style={{ position: "absolute", right: 5, top: 5, width: 4, height: 4, borderRadius: 999, background: palette.brass }} />
              </span>
              <strong style={{ fontSize: 15, fontWeight: 600, letterSpacing: "-0.02em" }}>LeadX</strong>
            </div>
            <h1 style={{ margin: "28px 0 0", fontSize: 22, fontWeight: 600, letterSpacing: "-0.02em", lineHeight: 1.25 }}>LeadX needs a quick restart.</h1>
            <p style={{ margin: "10px 0 0", color: palette.muted, fontSize: 14, lineHeight: 1.6 }}>An unexpected application error occurred. Retry first; your server-side data is not deleted by this error.</p>
            <div style={{ marginTop: 24, paddingTop: 20, borderTop: `1px solid ${palette.line}` }}>
              <button type="button" onClick={() => reset()} style={{ minHeight: 36, padding: "9px 16px", borderRadius: 8, border: 0, background: palette.primary, color: palette.primaryInk, fontWeight: 600, fontSize: 13, cursor: "pointer", fontFamily: "inherit" }}>Retry LeadX</button>
            </div>
            <p style={{ margin: "16px 0 0", color: palette.faint, fontSize: 12 }}>If this keeps happening, contact LeadX support.</p>
          </section>
        </main>
      </body>
    </html>
  );
}
