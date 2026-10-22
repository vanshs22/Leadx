import { NextRequest, NextResponse } from "next/server";

const PUBLIC = new Set(["/", "/login", "/signup", "/accept-invite", "/verify-email", "/pricing", "/audit"]);
const PORTAL_PREFIXES = ["/client", "/portal"];
const PORTAL_ROUTES = new Set(["dashboard","growth","today","search","leads","companies","contacts","deals","sequences","compose","replies","notifications","pipeline","activities","reports","revenue","import","team","docs","settings","login","accept-invite"]);

function authCookie(req: NextRequest) {
  const cookies = req.cookies.getAll().filter(c => c.name === "leadx_session");
  return cookies.sort((a,b) => a.name.localeCompare(b.name, undefined, {numeric:true})).map(c => decodeURIComponent(c.value)).join("");
}

async function sessionStatus(req: NextRequest) {
  const token = authCookie(req);
  const url = (process.env.NEXT_PUBLIC_SUPABASE_URL || "").replace(/\/$/, "");
  const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "";
  if (!url || !anon) return { ok: false, access: "" };
  if (token) {
    try {
      const r = await fetch(`${url}/auth/v1/user`, { headers: { Authorization: `Bearer ${token}`, apikey: anon }, cache: "no-store" });
      if (r.ok) return { ok: true, access: token };
    } catch {}
  }
  const refresh = req.cookies.get("leadx_refresh")?.value || "";
  if (!refresh) return { ok: false, access: "" };
  try {
    const r = await fetch(`${url}/auth/v1/token?grant_type=refresh_token`, { method: "POST", headers: { "Content-Type": "application/json", apikey: anon }, body: JSON.stringify({ refresh_token: refresh }), cache: "no-store" });
    if (!r.ok) return { ok: false, access: "" };
    const data = await r.json();
    return { ok: true, access: String(data.access_token || ""), refresh: String(data.refresh_token || refresh), expires: Number(data.expires_in || 3600) };
  } catch { return { ok: false, access: "" }; }
}

function csp(nonce: string) {
  const supabase = (process.env.NEXT_PUBLIC_SUPABASE_URL || "").replace(/\/$/, "");
  let supabaseOrigin = "";
  let supabaseWs = "";
  try {
    const parsed = new URL(supabase);
    supabaseOrigin = parsed.origin;
    supabaseWs = parsed.origin.replace(/^http/, "ws");
  } catch {}
  return ["default-src 'self'","base-uri 'self'","object-src 'none'","frame-ancestors 'none'","img-src 'self' data: blob: https:","font-src 'self' data: https:","style-src 'self' 'unsafe-inline'",`script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,`connect-src 'self' ${supabaseOrigin} ${supabaseWs}`.trim(),"form-action 'self'","frame-src 'none'",...(process.env.NODE_ENV === "production" ? ["upgrade-insecure-requests"] : [])].join("; ");
}

export async function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const nonce = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(16))));
  const policy = csp(nonce);
  const requestHeaders = new Headers(req.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", policy);
  let res: NextResponse = NextResponse.next({ request: { headers: requestHeaders } });

  if (pathname === "/backend" || pathname.startsWith("/backend/")) {
    res = NextResponse.next({ request: { headers: requestHeaders } });
  } else {
    for (const prefix of PORTAL_PREFIXES) {
      if (pathname === prefix || pathname.startsWith(`${prefix}/`)) {
        const rest = pathname.slice(prefix.length) || "/"; const first = rest.replace(/^\//, "").split("/")[0];
        if (!first || PORTAL_ROUTES.has(first)) { const url=req.nextUrl.clone(); url.pathname=rest === "/" ? "/dashboard" : rest; res=NextResponse.redirect(url,308); res.headers.set("Content-Security-Policy",policy); return res; }
      }
    }
    if (pathname === "/crm" || (pathname.startsWith("/crm/") && PORTAL_ROUTES.has(pathname.slice(5).split("/")[0]))) {
      const url=req.nextUrl.clone(); url.pathname=pathname === "/crm" ? "/dashboard" : pathname.slice(4); res=NextResponse.redirect(url,308); res.headers.set("Content-Security-Policy",policy); return res;
    }
    const publicPath = [...PUBLIC].some(p => p === pathname || (p === "/audit" && pathname.startsWith("/audit/")));
    const protectedPath = pathname.startsWith("/admin") || (pathname.startsWith("/") && !publicPath && !pathname.startsWith("/_next/") && !pathname.startsWith("/api/"));
    if (protectedPath && pathname !== "/admin/login") {
      const session = await sessionStatus(req);
      if (!session.ok) { const url=req.nextUrl.clone(); url.pathname=pathname.startsWith("/admin") ? "/admin/login" : "/login"; url.searchParams.set("next",pathname); res=NextResponse.redirect(url); res.headers.set("Content-Security-Policy",policy); return res; }
      res=NextResponse.next({ request: { headers: requestHeaders } });
      if (session.refresh) {
        const secure = process.env.NODE_ENV === "production";
        res.cookies.set("leadx_session", session.access, {
          path: "/", maxAge: session.expires || 3600, httpOnly: true,
          sameSite: "lax", secure,
        });
        res.cookies.set("leadx_refresh", session.refresh, {
          path: "/", maxAge: 2592000, httpOnly: true,
          sameSite: "lax", secure,
        });
        res.cookies.set("leadx_csrf", crypto.randomUUID(), {
          path: "/", maxAge: 2592000, sameSite: "lax", secure,
        });
      }
    }
  }
  res.headers.set("Content-Security-Policy",policy);
  res.headers.set("X-Content-Type-Options","nosniff");
  res.headers.set("X-Frame-Options","DENY");
  res.headers.set("Referrer-Policy","strict-origin-when-cross-origin");
  res.headers.set("Permissions-Policy","camera=(), microphone=(), geolocation=()");
  if (process.env.NODE_ENV === "production") res.headers.set("Strict-Transport-Security","max-age=31536000; includeSubDomains");
  res.headers.set("Cross-Origin-Opener-Policy","same-origin");
  return res;
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
