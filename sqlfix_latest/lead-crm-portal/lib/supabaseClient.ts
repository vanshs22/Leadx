/**
 * Browser Supabase client — tenant-user auth only (anon key + RLS).
 */
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let _client: SupabaseClient | null = null;

function cleanEnv(v: string | undefined): string {
  return (v || "")
    .trim()
    .replace(/^["']|["']$/g, "") // strip accidental quotes
    .replace(/\/+$/, ""); // no trailing slash
}

export function getSupabase(): SupabaseClient {
  if (_client) return _client;

  const url = cleanEnv(process.env.NEXT_PUBLIC_SUPABASE_URL);
  const anonKey = cleanEnv(process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY);

  if (!url || !anonKey) {
    throw new Error(
      "Supabase env missing. In lead-crm-portal/frontend/.env.local set:\n" +
        "  NEXT_PUBLIC_SUPABASE_URL=https://YOUR_REF.supabase.co\n" +
        "  NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJ...\n" +
        "Then restart: stop npm/setup and run ./setup.sh again (Next only reads .env.local on start)."
    );
  }

  if (!/^https?:\/\//i.test(url)) {
    throw new Error(
      `Invalid NEXT_PUBLIC_SUPABASE_URL "${url}" — must start with https:// (e.g. https://xxxx.supabase.co). ` +
        "Fix .env.local and restart the frontend."
    );
  }

  try {
    // Throws if URL is still not parseable
    // eslint-disable-next-line no-new
    new URL(url);
  } catch {
    throw new Error(
      `Invalid NEXT_PUBLIC_SUPABASE_URL "${url}" — not a valid HTTP(S) URL. Check for typos/spaces in .env.local and restart Next.`
    );
  }

  _client = createClient(url, anonKey, {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: true },
  });
  return _client;
}
