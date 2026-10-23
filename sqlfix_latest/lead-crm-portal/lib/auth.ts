import { getSupabase } from "./supabaseClient";
import { clearTenant } from "./tenant";
import { getApiBase } from "./config";

let cachedAccessToken: string | null = null;
let cachedTokenAt = 0;
let cachedSession: any = null;
let sessionReadPromise: Promise<any> | null = null;
let authListenerStarted = false;
const TOKEN_CACHE_MS = 60_000;

function readCsrf(): string {
  if (typeof document === "undefined") return "";
  return document.cookie.split(";").map(x => x.trim()).find(x => x.startsWith("leadx_csrf="))?.slice("leadx_csrf=".length) || "";
}

async function establishServerSession(session: any) {
  if (!session?.access_token) return;
  await fetch(`${getApiBase()}/auth/session`, {
    method: "POST", credentials: "include",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.access_token}` },
    body: JSON.stringify({ access_token: session.access_token, refresh_token: session.refresh_token || null }),
  });
}

function ensureAuthListener() {
  if (authListenerStarted || typeof window === "undefined") return;
  authListenerStarted = true;
  try {
    getSupabase().auth.onAuthStateChange((_event, session) => {
      cachedSession = session || null;
      cachedAccessToken = session?.access_token || null;
      cachedTokenAt = Date.now();
    });
  } catch {}
}

export async function getSessionFast() {
  ensureAuthListener();
  if (cachedSession) return cachedSession;
  if (!sessionReadPromise) {
    sessionReadPromise = getSupabase().auth.getSession().then(async ({ data }) => {
      if (data.session) {
        cachedSession = data.session;
        cachedAccessToken = data.session.access_token || null;
        cachedTokenAt = Date.now();
        return cachedSession;
      }
      try {
        const res = await fetch(`${getApiBase()}/auth/session`, { credentials: "include", cache: "no-store" });
        if (!res.ok) {
          if (res.status === 401) {
            const refreshed = await fetch(`${getApiBase()}/auth/session/refresh`, { method: "POST", credentials: "include", headers: { "X-CSRF-Token": readCsrf() } });
            if (refreshed.ok) {
              const rr = await fetch(`${getApiBase()}/auth/session`, { credentials: "include", cache: "no-store" });
              if (rr.ok) {
                const j = await rr.json();
                cachedSession = { user: j.user, access_token: null, refresh_token: null };
                cachedAccessToken = null; cachedTokenAt = Date.now(); return cachedSession;
              }
            }
          }
          cachedSession = null; cachedAccessToken = null; return null;
        }
        const j = await res.json();
        cachedSession = { user: j.user, access_token: null, refresh_token: null };
        cachedAccessToken = null; cachedTokenAt = Date.now();
        return cachedSession;
      } catch { cachedSession = null; cachedAccessToken = null; return null; }
    }).finally(() => { sessionReadPromise = null; });
  }
  return sessionReadPromise;
}

export function primeSession(session: any) {
  cachedSession = session || null;
  cachedAccessToken = session?.access_token || null;
  cachedTokenAt = Date.now();
  ensureAuthListener();
}

export async function getAccessToken(): Promise<string | null> {
  const now = Date.now();
  if (cachedAccessToken && now - cachedTokenAt < TOKEN_CACHE_MS) return cachedAccessToken;
  const session = await getSessionFast();
  cachedAccessToken = session?.access_token || null;
  cachedTokenAt = now;
  return cachedAccessToken;
}

async function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<T>((_, reject) => {
        timer = setTimeout(() => reject(new Error(`${label} timed out. Please retry.`)), ms);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export async function signInWithPassword(email: string, password: string) {
  const client = getSupabase();
  const result: Awaited<ReturnType<typeof client.auth.signInWithPassword>> = await withTimeout(
    client.auth.signInWithPassword({ email, password }),
    10000,
    "Sign-in"
  );
  if (result.error) throw new Error(result.error.message);
  // Reuse the just-created session immediately. SessionGate no longer needs
  // to perform a second session read immediately after successful login.
  await establishServerSession(result.data.session);
  primeSession(result.data.session);
  return result.data.session;
}

export async function signUpWithPassword(email: string, password: string) {
  const { data, error } = await getSupabase().auth.signUp({ email, password });
  if (error) throw new Error(error.message);
  return data;
}

/** Passwordless — Supabase emails a one-tap link. */
export async function signInWithMagicLink(email: string) {
  const redirectTo =
    typeof window !== "undefined" ? `${window.location.origin}/dashboard` : undefined;
  const { error } = await getSupabase().auth.signInWithOtp({
    email,
    options: redirectTo ? { emailRedirectTo: redirectTo } : undefined,
  });
  if (error) throw new Error(error.message);
}

export async function signOut() {
  clearTenant();
  cachedAccessToken = null;
  cachedTokenAt = 0;
  cachedSession = null;
  try { await fetch(`${getApiBase()}/auth/session`, { method: "DELETE", credentials: "include", headers: { "X-CSRF-Token": readCsrf() } }); } catch {}
  await getSupabase().auth.signOut();
}

export async function isLoggedIn(): Promise<boolean> {
  return !!(await getAccessToken());
}
