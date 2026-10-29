/**
 * Browser API routing. Prefer same-origin /backend, but in GitHub Codespaces
 * fall back to the forwarded FastAPI port when the Next proxy is unavailable.
 */
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "::1"]);

export function isLocalAuthProfile(): boolean {
  return ["1", "true", "yes", "on"].includes(String(process.env.NEXT_PUBLIC_LEADX_LOCAL_AUTH || "").trim().toLowerCase());
}

export function isRemoteBrowser() {
  if (typeof window === "undefined") return false;
  const host = window.location.hostname;
  return host.includes("github.dev") || host.includes("app.github.dev") ||
    host.includes("webcontainer") || host.includes("codesandbox") || host.includes("githubpreview.dev") ||
    (host !== "localhost" && host !== "127.0.0.1");
}

/** True for profiles where a portal container must not target itself. */
export function isHardenedPortalProfile(): boolean {
  const profile = String(
    process.env.LEADX_PORTAL_PROFILE ||
      process.env.PORTAL_PROFILE ||
      process.env.ENVIRONMENT ||
      ""
  ).toLowerCase();
  return process.env.NODE_ENV === "production" ||
    ["production", "staging", "container", "hardened"].includes(profile);
}

function randomRequestId(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") return globalThis.crypto.randomUUID();
  return `portal-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** Request IDs are correlation values, not user or tenant identifiers. */
export function createPortalRequestId(value?: string | null): string {
  const candidate = String(value || "").trim();
  return /^[A-Za-z0-9._:-]{1,128}$/.test(candidate) ? candidate : randomRequestId();
}

export function isLoopbackApiUpstream(value: string): boolean {
  try {
    const hostname = new URL(value).hostname.replace(/^\[|\]$/g, "").toLowerCase();
    return LOOPBACK_HOSTS.has(hostname);
  } catch {
    return false;
  }
}

/**
 * Resolve only the server-side rewrite target. API_UPSTREAM is deliberately
 * not public, so browser calls continue to use same-origin /backend.
 */
export function getServerApiUpstream(): string {
  const hardened = isHardenedPortalProfile();
  const configured = String(process.env.API_UPSTREAM || "").trim();
  const upstream = configured || (hardened ? "http://api:8000" : "http://127.0.0.1:8000");
  let parsed: URL;
  try {
    parsed = new URL(upstream);
  } catch {
    throw new Error("API_UPSTREAM must be an absolute HTTP(S) URL");
  }
  if (!/^https?:$/.test(parsed.protocol) || parsed.username || parsed.password || parsed.hash) {
    throw new Error("API_UPSTREAM must be an absolute HTTP(S) URL without credentials");
  }
  if (hardened && isLoopbackApiUpstream(upstream)) {
    throw new Error("Loopback API_UPSTREAM is not allowed in the portal container profile");
  }
  return upstream.replace(/\/$/, "");
}

export function getForwardedApiBase(): string | null {
  if (typeof window === "undefined" || !isRemoteBrowser()) return null;
  const { protocol, hostname } = window.location;
  const forwarded = hostname.replace(/-3000(?=\.|$)/, "-8000");
  if (forwarded === hostname) return null;
  return `${protocol}//${forwarded}`;
}

export function getApiBase(): string {
  if (typeof window !== "undefined") {
    const configured = process.env.NEXT_PUBLIC_API_URL?.trim().replace(/\/$/, "");
    if (configured) {
      if (isRemoteBrowser() && /localhost|127\.0\.0\.1/.test(configured)) return "/backend";
      return configured;
    }
    return "/backend";
  }
  return getServerApiUpstream();
}

export function getApiBases(): string[] {
  const bases = [getApiBase()];
  const forwarded = getForwardedApiBase();
  if (forwarded && !bases.includes(forwarded)) bases.push(forwarded);
  return bases;
}

/**
 * Polling is a bounded safety net, not an indefinite request loop. The API
 * runtime uses POLLING_DEADLINE_SECONDS; NEXT_PUBLIC is supported because
 * values in a client bundle must be explicitly public.
 */
export function getPortalPollingDeadlineMs(): number {
  const raw = process.env.NEXT_PUBLIC_POLLING_DEADLINE_SECONDS || process.env.POLLING_DEADLINE_SECONDS || "300";
  const seconds = Number(raw);
  return (Number.isFinite(seconds) && seconds > 0 ? Math.min(seconds, 86400) : 300) * 1000;
}
