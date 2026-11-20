// Layout scan: every page x 3 viewports x light/dark. Flags page-level horizontal overflow, clipped tables, and error text.
import { chromium } from "../../lead-crm-portal/node_modules/@playwright/test/index.mjs";
import fs from "fs";
const seed = JSON.parse(fs.readFileSync(process.env.SEED_OUT || "/projects/sandbox/.tools/browser_seed.json")); const BASE = process.env.PORTAL_URL;
const SHOTS = process.env.SHOT_DIR || "/projects/sandbox/shots"; fs.mkdirSync(SHOTS, { recursive: true });
const VIEWS = [["mobile", 390, 844], ["tablet", 820, 1100], ["desktop", 1360, 900]]; const THEMES = ["light", "dark"];
const client = ["dashboard", "leads", "companies", "contacts", "deals", "pipeline", "search", "import", "sequences", "automations", "plays", "duplicates", "replies", "reports", "team", "settings", "settings/billing", "approvals", "today", "intelligence"].map(x => "/" + x);
const pub = ["/", "/pricing", "/privacy", "/terms", "/contact", "/login", "/signup"];
const admin = ["/admin", "/admin/tenants", "/admin/runs", "/admin/jobs", "/admin/leads", "/admin/search", "/admin/agents", "/admin/security"];
const R = [];
async function scan(page, path, label) {
  await page.goto(BASE + path, { waitUntil: "domcontentloaded" }); await page.waitForLoadState("networkidle", { timeout: 12000 }).catch(() => {}); await page.waitForTimeout(350);
  const m = await page.evaluate(() => {
    const de = document.documentElement; const over = de.scrollWidth - window.innerWidth;
    const wide = [...document.querySelectorAll("table")].filter(t => { const c = t.closest(".overflow-x-auto, [class*=overflow]"); return !c && t.scrollWidth > t.parentElement.clientWidth + 2; }).length;
    const offscreen = [...document.querySelectorAll("button, a, input, select")].filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && (r.right > window.innerWidth + 2 || r.left < -2) && !e.closest("[class*=overflow]") && getComputedStyle(e).position !== "fixed"; }).length;
    return { over, wide, offscreen, text: document.body.innerText.slice(0, 4000), dark: de.classList.contains("dark") };
  });
  const flags = []; if (m.over > 2) flags.push(`page scrolls sideways by ${m.over}px`); if (m.wide) flags.push(`${m.wide} table(s) wider than container without a scroller`); if (m.offscreen > 0) flags.push(`${m.offscreen} control(s) off-screen`);
  if (/temporarily unavailable|application error|something went wrong/i.test(m.text)) flags.push("error text shown");
  R.push({ ...label, path, flags }); return m;
}
for (const theme of THEMES) for (const [vn, w, h] of VIEWS) {
  const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: w, height: h }, colorScheme: theme }); const page = await ctx.newPage();
  await ctx.addInitScript(t => { try { localStorage.setItem("theme", t); localStorage.setItem("leadx-theme", t); localStorage.setItem("leadx_theme", t); } catch {} }, theme);
  for (const p of pub) { const m = await scan(page, p, { role: "visitor", vn, theme }); if (!m) continue; }
  await page.goto(BASE + "/login"); await page.fill('input[type="email"]', seed.client_email); await page.fill('input[type="password"]', seed.password); await page.click('button[type="submit"]'); await page.waitForURL(u => !u.pathname.includes("login"), { timeout: 20000 });
  if (theme === "dark") { const t = page.getByText("Dark mode", { exact: true }).first(); if (await t.isVisible().catch(() => false)) await t.click(); }
  for (const p of client) { const m = await scan(page, p, { role: "client", vn, theme }); if (m && ["/leads", "/dashboard"].includes(p)) await page.screenshot({ path: `${SHOTS}/${vn}-${theme}${p.replace("/", "-")}.png` }); }
  await b.close();
  const b2 = await chromium.launch(); const ctx2 = await b2.newContext({ viewport: { width: w, height: h }, colorScheme: theme }); const p2 = await ctx2.newPage();
  await p2.goto(BASE + "/admin/login"); await p2.fill('input[type="email"]', seed.admin_email); await p2.fill('input[type="password"]', seed.password); await p2.click('button[type="submit"]'); await p2.waitForURL(u => !u.pathname.includes("login"), { timeout: 20000 });
  for (const p of admin) await scan(p2, p, { role: "admin", vn, theme });
  await b2.close();
}
fs.writeFileSync(process.env.RESP_OUT || "browser_responsive.json", JSON.stringify(R, null, 1));
const bad = R.filter(r => r.flags.length); console.log(`SUMMARY-RESPONSIVE scans=${R.length} problems=${bad.length}`);
const by = {}; for (const r of bad) (by[`${r.role} ${r.path}`] ||= []).push(`${r.vn}/${r.theme}: ${r.flags.join(", ")}`);
for (const [k, v] of Object.entries(by)) console.log("PROBLEM", k, "=>", v.slice(0, 3).join(" | "));
