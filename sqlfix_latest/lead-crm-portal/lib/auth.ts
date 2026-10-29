import { getSupabase } from "./supabaseClient";
import { clearTenant } from "./tenant";
import { getApiBase, isLocalAuthProfile } from "./config";

let cachedAccessToken: string | null = null;
let cachedTokenAt = 0;
let cachedSession: any = null;
let sessionReadPromise: Promise<any> | null = null;
let serverSessionPromise: Promise<boolean> | null = null;
let serverSessionAt = 0;
let authListenerStarted = false;
const TOKEN_CACHE_MS = 60_000;

function readCsrf(): string {
  if (typeof document === "undefined") return "";
  return document.cookie.split(";").map(x => x.trim()).find(x => x.startsWith("leadx_csrf="))?.slice("leadx_csrf=".length) || "";
}

async function establishLocalServerSession(email: string, password: string) {
  const response = await fetch(`${getApiBase()}/auth/session`, {
    method: "POST", credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) {
    let detail = "Invalid email or password";
    try {
      const body = await response.json() as { detail?: string; message?: string; error?: string };
      detail = body.detail || body.message || body.error || detail;
    } catch {}
    throw new Error(detail);
  }
  return response.json() as Promise<{ ok: boolean; user: any }>;
}

async function readLocalServerSession(): Promise<any | null> {
  try {
    let response = await fetch(`${getApiBase()}/auth/session`, { credentials: "include", cache: "no-store" });
    if (!response.ok && response.status === 401) {
      response = await fetch(`${getApiBase()}/auth/session/refresh`, {
        method: "POST", credentials: "include", headers: { "X-CSRF-Token": readCsrf() },
      });
      if (response.ok) response = await fetch(`${getApiBase()}/auth/session`, { credentials: "include", cache: "no-store" });
    }
    if (!response.ok) return null;
    const body = await response.json();
    return body?.user ? { user: body.user, access_token: null, refresh_token: null } : null;
  } catch {
    return null;
  }
}

async function establishServerSession(session: any) {
  if (!session?.access_token) return;
  const response = await fetch(`${getApiBase()}/auth/session`, {
    method: "POST", credentials: "include",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.access_token}` },
    body: JSON.stringify({ access_token: session.access_token, refresh_token: session.refresh_token || null }),
  });
  if (!response.ok) {
    let detail = "Session exchange failed";
    try {
      const body = await response.json() as { detail?: string; message?: string };
      detail = body.detail || body.message || detail;
    } catch {}
    throw new Error(`${detail}. Please try signing in again.`);
  }
  serverSessionAt = Date.now();
}

/** Refresh the HttpOnly LeadX session bridge from the current Supabase session. */
export async function renewServerSession(): Promise<boolean> {
  if (serverSessionPromise) return serverSessionPromise;
  serverSessionPromise = (async () => {
    try {
      if (isLocalAuthProfile()) return !!(await readLocalServerSession());
      const { data } = await getSupabase().auth.getSession();
      const session = data.session;
      if (!session?.access_token) return false;
      cachedSession = session;
      cachedAccessToken = session.access_token || null;
      cachedTokenAt = Date.now();
      await establishServerSession(session);
      return true;
    } catch {
      return false;
    }
  })().finally(() => { serverSessionPromise = null; });
  return serverSessionPromise;
}

function ensureAuthListener() {
  if (isLocalAuthProfile() || authListenerStarted || typeof window === "undefined") return;
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
  if (isLocalAuthProfile()) {
    if (cachedSession) return cachedSession;
    if (!sessionReadPromise) {
      sessionReadPromise = readLocalServerSession().then((session) => {
        cachedSession = session;
        cachedAccessToken = null;
        cachedTokenAt = Date.now();
        return session;
      }).finally(() => { sessionReadPromise = null; });
    }
    return sessionReadPromise;
  }
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
  if (isLocalAuthProfile()) {
    const result = await withTimeout(establishLocalServerSession(email, password), 10000, "Sign-in");
    const session = { user: result.user, access_token: null, refresh_token: null };
    primeSession(session);
    return session;
  }
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
  if (!isLocalAuthProfile()) await getSupabase().auth.signOut();
}

export async function isLoggedIn(): Promise<boolean> {
  return !!(await getAccessToken());
}
