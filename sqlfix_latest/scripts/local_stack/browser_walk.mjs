// Real-browser walk: logs in through the actual forms and visits every page as client and as platform admin.
import { chromium } from "../../lead-crm-portal/node_modules/@playwright/test/index.mjs";
import fs from "fs";
const seed = JSON.parse(fs.readFileSync(process.env.SEED_OUT || "/projects/sandbox/.tools/browser_seed.json"));
const BASE = process.env.PORTAL_URL, OUT = process.env.WALK_OUT || "browser_walk.json";
const IGNORE = [/realtime\/v1\/websocket/, /favicon/];
const results = [];
async function session(role) {
  const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 1360, height: 900 } }); const page = await ctx.newPage();
  const state = { bad: [] };
  page.on("console", m => { if (m.type() === "error" && !IGNORE.some(r => r.test(m.text()))) state.bad.push("console: " + m.text().slice(0, 140)); });
  page.on("pageerror", e => state.bad.push("pageerror: " + String(e).slice(0, 140)));
  page.on("response", r => { if (r.status() >= 400 && !IGNORE.some(x => x.test(r.url()))) state.bad.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`); });
  return { b, ctx, page, state };
}
async function login(page, url, email) {
  await page.goto(BASE + url); await page.fill('input[type="email"]', email); await page.fill('input[type="password"]', seed.password);
  await page.click('button[type="submit"]'); await page.waitForURL(u => !u.pathname.includes("login"), { timeout: 20000 });
}
async function walk(role, paths, page, state) {
  for (const p of paths) {
    state.bad.length = 0; let status = "ok", title = "", text = "";
    try {
      const resp = await page.goto(BASE + p, { waitUntil: "domcontentloaded", timeout: 30000 });
      await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
      await page.waitForTimeout(500);
      title = (await page.locator("h1").first().textContent({ timeout: 2000 }).catch(() => "")) || "";
      text = await page.locator("body").innerText();
      if (resp && resp.status() >= 400) status = "http" + resp.status();
    } catch (e) { status = "nav-fail: " + String(e).slice(0, 80); }
    const flags = [];
    if (/temporarily unavailable|something went wrong|application error|unhandled|failed to load/i.test(text)) flags.push("error-text");
    if (new URL(page.url()).pathname.includes("login") && !p.includes("login")) flags.push("redirected-to-login");
    results.push({ role, path: p, status, h1: title.trim().slice(0, 50), flags, bad: [...new Set(state.bad)].slice(0, 6) });
  }
}
const clientPaths = ["dashboard", "growth", "today", "intelligence", "approvals", "search", "leads", "companies", "contacts", "duplicates", "deals", "pipeline", "sequences", "automations", "agents", "compose", "replies", "reports", "reports/deals", "revenue", "import", "logs", "notifications", "team", "settings", "settings/billing", "settings/email", "settings/integrations", "onboarding"].map(x => "/" + x);
const adminPaths = fs.readdirSync(new URL("../../lead-crm-portal/app/(admin)/admin", import.meta.url).pathname, { withFileTypes: true }).filter(d => d.isDirectory() && d.name !== "login").map(d => "/admin/" + d.name).concat(["/admin"]);
let s = await session("client"); await login(s.page, "/login", seed.client_email); await walk("client", clientPaths, s.page, s.state); await s.b.close();
s = await session("admin"); await login(s.page, "/admin/login", seed.admin_email); await walk("admin", adminPaths, s.page, s.state);
// a client must not get into admin pages
const c2 = await session("client"); await login(c2.page, "/login", seed.client_email); await c2.page.goto(BASE + "/admin"); await c2.page.waitForTimeout(1500);
results.push({ role: "client", path: "/admin (access check)", status: "ok", h1: "", flags: /\/admin\/login|\/dashboard|\/login/.test(c2.page.url()) || /not authori|forbidden|denied/i.test(await c2.page.locator("body").innerText()) ? [] : ["CLIENT-REACHED-ADMIN"], bad: [] });
await c2.b.close(); await s.b.close();
fs.writeFileSync(OUT, JSON.stringify(results, null, 1));
const probs = results.filter(r => r.status !== "ok" || r.flags.length || r.bad.length);
console.log(`pages: ${results.length}, with problems: ${probs.length}`);
for (const r of probs) console.log(`${r.role} ${r.path} [${r.status}] ${r.flags.join(",")} ${r.bad.join(" | ")}`.slice(0, 330));
