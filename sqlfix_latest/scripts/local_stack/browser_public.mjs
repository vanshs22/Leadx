// Signed-out visitor: every public/legal page must open (no login redirect), have one h1, and every internal link on them must resolve.
import { chromium } from "../../lead-crm-portal/node_modules/@playwright/test/index.mjs";
import fs from "fs";
const BASE = process.env.PORTAL_URL; const R = [];
const PAGES = ["/", "/pricing", "/sample", "/privacy", "/terms", "/dpa", "/security", "/cookies", "/acceptable-use", "/subprocessors", "/contact", "/login", "/signup"];
const b = await chromium.launch(); const page = await (await b.newContext({ viewport: { width: 1360, height: 900 } })).newPage();
const errs = []; page.on("pageerror", e => errs.push(String(e).slice(0, 100))); page.on("console", m => { if (m.type() === "error" && !/realtime|favicon|403|401/.test(m.text())) errs.push("console: " + m.text().slice(0, 120)); });
const links = new Set();
for (const p of PAGES) {
  errs.length = 0; const resp = await page.goto(BASE + p, { waitUntil: "domcontentloaded" }); await page.waitForLoadState("networkidle").catch(() => {});
  const url = new URL(page.url()); const h1 = await page.locator("h1").count(); const body = (await page.locator("body").innerText()).length;
  const flags = []; if (url.pathname !== p && !(p === "/" && url.pathname === "/")) flags.push("redirected to " + url.pathname); if (resp.status() !== 200) flags.push("http " + resp.status()); if (h1 !== 1) flags.push(`h1 count ${h1}`); if (body < 400 && !["/login", "/signup", "/sample"].includes(p)) flags.push("thin page"); if (errs.length) flags.push(...errs.slice(0, 2));
  for (const href of await page.locator("a[href]").evaluateAll(a => a.map(x => x.getAttribute("href")))) if (href && href.startsWith("/") && !href.startsWith("//")) links.add(href.split("#")[0].split("?")[0]);
  R.push({ page: p, status: flags.length ? "FAIL" : "PASS", detail: flags.join("; ") });
}
for (const f of ["/robots.txt", "/sitemap.xml", "/.well-known/security.txt"]) { const r = await page.request.get(BASE + f); R.push({ page: f, status: r.status() === 200 && (await r.text()).length > 20 ? "PASS" : "FAIL", detail: "http " + r.status() }); }
const r404 = await page.goto(BASE + "/definitely-not-a-page-xyz"); R.push({ page: "404 page for signed-out visitor", status: [404, 200].includes(r404.status()) ? "PASS" : "FAIL", detail: "http " + r404.status() + " -> " + new URL(page.url()).pathname });
const broken = [];
for (const l of links) { if (!l || PAGES.includes(l) || l.startsWith("/_next") || l === "/dashboard") continue; const r = await page.request.get(BASE + l, { maxRedirects: 0 }); if (r.status() >= 400) broken.push(`${l} ${r.status()}`); }
R.push({ page: `internal links from public pages (${links.size} unique)`, status: broken.length ? "FAIL" : "PASS", detail: broken.join(", ") });
const hdr = (await page.request.get(BASE + "/")).headers();
for (const h of ["content-security-policy", "x-frame-options", "x-content-type-options", "referrer-policy", "permissions-policy"]) R.push({ page: "header " + h, status: hdr[h] ? "PASS" : "FAIL", detail: (hdr[h] || "missing").slice(0, 70) });
R.push({ page: "CSP forbids framing and foreign form targets", status: /frame-ancestors 'none'/.test(hdr["content-security-policy"] || "") && /form-action|default-src 'self'/.test(hdr["content-security-policy"] || "") ? "PASS" : "FAIL", detail: "" });
// legal links reachable from signup and login
for (const p of ["/signup", "/login"]) { await page.goto(BASE + p); const hs = await page.locator('a[href="/terms"], a[href="/privacy"]').count(); R.push({ page: p + " links to Terms/Privacy", status: hs >= 1 ? "PASS" : "FAIL", detail: String(hs) }); }
await b.close(); fs.writeFileSync(process.env.PUBLIC_OUT || "browser_public.json", JSON.stringify(R, null, 1));
for (const r of R) console.log(`${r.status} [public] ${r.page}${r.detail && r.status === "FAIL" ? "  -> " + r.detail : ""}`);
console.log("SUMMARY-PUBLIC", R.filter(r => r.status === "PASS").length, "pass", R.filter(r => r.status === "FAIL").length, "fail");
