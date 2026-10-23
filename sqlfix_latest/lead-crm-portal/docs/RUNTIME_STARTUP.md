# LeadX frontend runtime startup

- The frontend intentionally uses the standard Next dev compiler (`next dev`) rather than the Turbopack flag.
- This avoids the `@vercel/turbopack-next/internal/font/google/font-display` compiler failure in the current Codespaces dependency environment.
- Google `next/font` imports are not used; the existing `--font-sans`, `--font-display`, and `--font-mono` CSS variable contract remains intact.
- For the full Codespaces runtime, use `leadx-runtime-fix-2026-09-28.sh`. It stops stale Uvicorn/Next processes, starts the canonical `app.app_service:app` on port 8000, validates the CRM route contract, and then starts Next on port 3000.
