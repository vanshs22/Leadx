"use client";

export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en">
      <body style={{ margin: 0, fontFamily: "system-ui, sans-serif", background: "#070a12", color: "#f8fafc" }}>
        <main style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 24 }}>
          <section style={{ maxWidth: 560, width: "100%", padding: 28, border: "1px solid #334155", borderRadius: 24, background: "#0d121d" }}>
            <strong style={{ letterSpacing: ".08em" }}>LEADX</strong>
            <h1 style={{ marginTop: 24 }}>LeadX needs a quick restart.</h1>
            <p style={{ color: "#cbd5e1", lineHeight: 1.6 }}>An unexpected application error occurred. Retry first; your server-side data is not deleted by this error.</p>
            <button onClick={() => reset()} style={{ marginTop: 12, padding: "10px 16px", borderRadius: 12, border: 0, background: "#7c5cff", color: "white", fontWeight: 700 }}>Retry LeadX</button>
          </section>
        </main>
      </body>
    </html>
  );
}
