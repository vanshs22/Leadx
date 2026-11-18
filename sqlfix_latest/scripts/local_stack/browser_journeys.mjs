// A tester's pass through the real portal: every step uses the UI the way a client / platform admin would.
import { chromium } from "../../lead-crm-portal/node_modules/@playwright/test/index.mjs";
import fs from "fs";
const seed = JSON.parse(fs.readFileSync(process.env.SEED_OUT || "/projects/sandbox/.tools/browser_seed.json"));
const BASE = process.env.PORTAL_URL, API = process.env.API_URL, SHOTS = process.env.SHOT_DIR || "/projects/sandbox/shots";
fs.mkdirSync(SHOTS, { recursive: true });
const R = []; const IGN = [/realtime\/v1\/websocket/, /favicon/, /platform\/me/];
const SVC = { apikey: process.env.SERVICE_JWT, Authorization: "Bearer " + process.env.SERVICE_JWT };
const rest = async (path) => (await fetch(process.env.SUPABASE_URL + "/rest/v1/" + path, { headers: SVC })).json();
async function open(role) {
  const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 1360, height: 900 } }); const page = await ctx.newPage(); const net = [];
  page.on("response", r => { if (r.status() >= 500 && !IGN.some(x => x.test(r.url()))) net.push(`${r.status()} ${new URL(r.url()).pathname}`); });
  page.on("pageerror", e => net.push("pageerror " + String(e).slice(0, 100)));
  page.on("dialog", d => d.accept());
  return { b, page, net, role };
}
async function step(S, name, fn) {
  S.net.length = 0; const t0 = Date.now();
  try { await fn(S.page); if (S.net.length) throw new Error("server errors during step: " + [...new Set(S.net)].join(", ")); R.push({ role: S.role, step: name, status: "PASS", ms: Date.now() - t0 }); }
  catch (e) { R.push({ role: S.role, step: name, status: "FAIL", error: String(e.message || e).split("\n")[0].slice(0, 260) }); await S.page.screenshot({ path: `${SHOTS}/FAIL-${S.role}-${R.length}.png` }).catch(() => {}); }
}
const settle = async (p) => { await p.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {}); await p.waitForTimeout(400); };
const expectText = async (p, text, timeout = 12000) => { await p.getByText(text, { exact: false }).first().waitFor({ timeout }); };
const login = async (p, url, email, pw) => { await p.goto(BASE + url); await p.fill('input[type="email"]', email); await p.fill('input[type="password"]', pw); await p.click('button[type="submit"]'); };

// ------------------------------------------------------------------ CLIENT
const C = await open("client");
await step(C, "login rejects a wrong password and stays on the login page", async p => {
  await login(p, "/login", seed.client_email, "wrong-password-1"); await p.waitForTimeout(2500);
  if (!p.url().includes("/login")) throw new Error("left login page with a bad password");
  if (!/invalid|incorrect|failed|wrong/i.test(await p.locator("body").innerText())) throw new Error("no error message shown");
});
await step(C, "login succeeds and lands on the dashboard", async p => { await login(p, "/login", seed.client_email, seed.password); await p.waitForURL(/dashboard/, { timeout: 20000 }); await expectText(p, "Browser Agency"); await p.screenshot({ path: `${SHOTS}/client-dashboard.png` }); });
await step(C, "leads page lists the delivered leads", async p => { await p.goto(BASE + "/leads"); await settle(p); await expectText(p, "Austin Bright Dental"); await expectText(p, "Dallas Dental Care"); await p.screenshot({ path: `${SHOTS}/client-leads.png` }); });
await step(C, "smart search understands plain English and filters the list", async p => {
  await p.getByRole("button", { name: "Smart search" }).click();
  await p.getByLabel("Search leads").fill("hot dental in Austin with email"); await expectText(p, "Understood as", 15000); await settle(p);
  const body = await p.locator("body").innerText();
  if (!body.includes("Austin Bright Dental")) throw new Error("expected lead missing"); if (body.includes("Dallas Dental Care") || body.includes("Austin Iron Gym")) throw new Error("filter did not exclude other leads");
  await p.screenshot({ path: `${SHOTS}/client-smart-search.png` });
  await p.getByRole("button", { name: "Alert me about new matches" }).click(); await expectText(p, "Saved.", 8000);
  const saved = await rest(`saved_views?tenant_id=eq.${seed.tenant_id}&entity=eq.smart_search&select=name`); if (saved.length !== 1) throw new Error("saved search not stored");
});
await step(C, "lead lifecycle board shows leads and a card can be dragged to another stage", async p => {
  await p.goto(BASE + "/pipeline"); await settle(p); await expectText(p, "Austin Bright Dental");
  await p.screenshot({ path: `${SHOTS}/client-pipeline.png` });
  const card = p.getByText("Austin Bright Dental", { exact: false }).first();
  const cols = await p.locator("[data-stage], [data-column], section[aria-label], [role=list]").count();
  const target = p.getByText(/^Contacted/i).first();
  await card.dragTo(target, { timeout: 8000 }); await p.waitForTimeout(1500);
  const rows = await rest(`lead_assignments?id=eq.${seed.lead_bright}&select=stage_slug`);
  if (rows[0]?.stage_slug === "new") throw new Error(`drag did not change the stage (columns detected: ${cols}, stage still new)`);
});
await step(C, "create a company from the form and see it in the list", async p => {
  await p.goto(BASE + "/companies"); await settle(p); await p.getByText("New company", { exact: true }).first().click();
  const form = p.locator("form").filter({ has: p.locator('input[placeholder="acme.com"]') }); await form.locator("input").first().fill("Journey Dental Co");
  await form.locator('input[placeholder="acme.com"]').fill("journeydental.test"); await p.getByRole("button", { name: "Create company" }).last().click(); await settle(p);
  await expectText(p, "Journey Dental Co"); const rows = await rest("companies?name=eq.Journey Dental Co&select=domain"); if (rows[0]?.domain !== "journeydental.test") throw new Error("company not saved correctly: " + JSON.stringify(rows));
});
await step(C, "duplicates page finds a duplicate account and merges it", async p => {
  await fetch(process.env.SUPABASE_URL + "/rest/v1/companies", { method: "POST", headers: { ...SVC, "Content-Type": "application/json" }, body: JSON.stringify({ tenant_id: seed.tenant_id, name: "JOURNEY DENTAL LLC", website: "https://www.journeydental.test", domain: null }) });
  await p.goto(BASE + "/duplicates"); await settle(p); await expectText(p, "matched on");
  await p.getByRole("button", { name: /Merge into kept record/ }).first().click(); await expectText(p, "Merged.", 10000);
  const rows = await rest(`companies?tenant_id=eq.${seed.tenant_id}&name=ilike.*journey dental*&select=id`); if (rows.length !== 1) throw new Error("expected 1 company after merge, got " + rows.length);
});
await step(C, "create a deal from the form", async p => {
  await p.goto(BASE + "/deals"); await settle(p); await p.getByText("New deal", { exact: true }).first().click();
  const form = p.locator("form").filter({ has: p.locator('input[type="number"]') }); await form.locator("input").first().fill("Journey retainer"); await form.locator('input[type="number"]').first().fill("4200");
  await form.locator('button[type="submit"], button:has-text("Save"), button:has-text("Create")').first().click(); await settle(p); await expectText(p, "Journey retainer");
});
await step(C, "import a CSV: preview then run, rows land in the CRM", async p => {
  fs.writeFileSync("/tmp/journey.csv", "name,domain,city\nImport One,importone.test,Austin\nImport Two,importtwo.test,Dallas\n");
  await p.goto(BASE + "/import"); await settle(p); await p.setInputFiles("#import-csv-file", "/tmp/journey.csv"); await p.getByRole("button", { name: "Preview" }).click(); await expectText(p, "Import One");
  await p.getByRole("button", { name: /^Import/ }).first().click(); await p.waitForTimeout(8000);
  const rows = await rest(`companies?tenant_id=eq.${seed.tenant_id}&name=like.Import*&select=name`); if (rows.length !== 2) throw new Error("expected 2 imported companies, got " + rows.length);
});
await step(C, "request new opportunities (discovery request)", async p => {
  await p.goto(BASE + "/search"); await settle(p); const ins = p.locator("main input"); await ins.nth(0).fill("physiotherapy clinic"); await ins.nth(1).fill("Austin, TX");
  await p.getByRole("button", { name: /Request opportunities/ }).first().click(); await p.waitForTimeout(2500);
  const rows = await rest(`discovery_requests?tenant_id=eq.${seed.tenant_id}&select=vertical,status`); if (!rows.some(r => /physio/i.test(r.vertical))) throw new Error("request not stored: " + JSON.stringify(rows));
});
await step(C, "invite a teammate", async p => { await p.goto(BASE + "/team"); await settle(p); await p.getByPlaceholder("email@company.com").fill("mate@leadx.test"); await p.getByRole("button", { name: "Send invite" }).click(); await expectText(p, "mate@leadx.test", 10000); });
await step(C, "client cannot open admin pages", async p => { await p.goto(BASE + "/admin/tenants"); await p.waitForTimeout(2500); const body = await p.locator("body").innerText(); if (/Browser Agency/.test(body) && /Tenants|Clients/i.test(body) && p.url().includes("/admin/tenants")) throw new Error("admin tenants page rendered for a client"); });
await step(C, "sign out ends the session; protected pages bounce to login", async p => {
  await p.goto(BASE + "/dashboard"); await settle(p); await p.getByText("Sign out", { exact: true }).first().click(); await p.waitForURL(/login|\/$/, { timeout: 10000 });
  await p.goto(BASE + "/leads"); await p.waitForTimeout(2000); if (!/login/.test(p.url())) throw new Error("still authenticated after sign out: " + p.url());
});
await C.b.close();

// ------------------------------------------------------------------ ADMIN
const A = await open("admin");
await step(A, "admin login rejects wrong password", async p => { await login(p, "/admin/login", seed.admin_email, "nope-nope-nope"); await p.waitForTimeout(2500); if (!p.url().includes("login")) throw new Error("left login with bad password"); });
await step(A, "admin login succeeds", async p => { await login(p, "/admin/login", seed.admin_email, seed.password); await p.waitForURL(u => !u.pathname.includes("login"), { timeout: 20000 }); await settle(p); await p.screenshot({ path: `${SHOTS}/admin-dashboard.png` }); });
await step(A, "admin sees the client workspace in Tenants", async p => { await p.goto(BASE + "/admin/tenants"); await settle(p); await expectText(p, "Browser Agency"); await p.screenshot({ path: `${SHOTS}/admin-tenants.png` }); });
await step(A, "admin sees the client's opportunity request and can approve it", async p => {
  await p.goto(BASE + "/admin/leads"); await settle(p); const found = await p.getByText(/physiotherapy/i).first().isVisible().catch(() => false);
  if (!found) { await p.goto(BASE + "/admin/search"); await settle(p); await expectText(p, "physiotherapy", 8000); }
  await p.screenshot({ path: `${SHOTS}/admin-requests.png` });
  await p.getByRole("button", { name: /Approve/ }).first().click(); await p.waitForTimeout(2500);
  const rows = await rest(`discovery_requests?tenant_id=eq.${seed.tenant_id}&select=status`); if (!rows.some(r => /approved|queued|running|completed/i.test(r.status))) throw new Error("request not approved: " + JSON.stringify(rows));
});
await step(A, "admin jobs, runs and scale pages render", async p => { for (const x of ["jobs", "runs", "scale", "usage", "health", "security"]) { await p.goto(BASE + "/admin/" + x); await settle(p); await p.waitForTimeout(300); } await p.screenshot({ path: `${SHOTS}/admin-security.png` }); });
await step(A, "admin sign out", async p => { await p.goto(BASE + "/admin"); await settle(p); await p.getByText(/Sign out/i).first().click(); await p.waitForTimeout(2000); await p.goto(BASE + "/admin/tenants"); await p.waitForTimeout(2000); if (!p.url().includes("login")) throw new Error("admin still signed in: " + p.url()); });
await A.b.close();
fs.writeFileSync(process.env.JOURNEY_OUT || "browser_journeys.json", JSON.stringify(R, null, 1));
for (const r of R) console.log(`${r.status} [${r.role}] ${r.step}${r.error ? "  -> " + r.error : ""}`);
console.log("SUMMARY", R.filter(r => r.status === "PASS").length, "pass", R.filter(r => r.status === "FAIL").length, "fail");
