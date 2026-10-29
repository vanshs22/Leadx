# LeadX — Kiro Session Handoff Context
_Last updated: 2026-10-25. This is a compact handoff extracted from the exported Kiro chat. It intentionally excludes credentials and the full transcript._

## How to use this file
Read this file at the start of a new Kiro conversation, then inspect only the specific source files needed for the next task. Treat the repository's current code as the source of truth; this file summarizes prior intent and reported state, not proof that every feature is correctly implemented.

## Project identity and working environment
- Product: **LeadX**, a lead-generation SaaS / CRM for discovering, enriching, qualifying, assigning, and delivering business leads to client accounts.
- Kiro workspace recorded in the exported session: `C:\Users\vansh\Leadx-1`.
- There is a working directory/subproject referenced in the latest work: `sqlfix_latest`.
- The project uses Supabase/Postgres, a frontend plus API/worker services, Docker Compose, and multiple lead-discovery/enrichment integrations.
- GitHub repo previously referenced by the user: `https://github.com/vanshs22/Leadx`.
- The user intends eventually to deploy the application on a VPS with a custom domain.
- User frequently tests locally and expects Kiro to run/build the project where possible, but Docker/Windows setup and PowerShell commands have previously failed intermittently.

## Critical operating preference: minimise Kiro credits
This is a standing requirement for every coding task:
- Minimise Kiro/AI credits, model calls, tool calls, and unnecessary context.
- Do not repeatedly scan the entire repository or re-investigate issues already resolved.
- Start by inspecting only the relevant files, current Git status, and any directly related tests/logs.
- Reuse working code and architecture; do not rebuild features or make broad rewrites.
- Make the smallest safe patch that fixes the actual issue.
- Avoid parallel subagents unless tasks are truly independent and the benefit outweighs coordination/token cost.
- Keep explanations concise. Do not run expensive builds or broad tests unless needed; run targeted tests first, then report exactly what was and was not verified.
- Do not confuse Kiro AI credits with AWS/Supabase/API credentials or provider quotas. User's request to minimise credits refers to Kiro/AI usage.
- Before changing files, state the concise plan and inspect the current implementation. Preserve existing functionality and unrelated work.

## Product goals and core workflow
LeadX should be a reliable, production-oriented lead-generation and client delivery platform, not a demo. The intended lifecycle:
1. Client configures an ICP / discovery request (industry, location, company criteria, target count, quality criteria).
2. Prospecting/ICP logic validates the request and expands it into appropriate geographic/search routes. Example: a gym search in New York should explore relevant subregions/routes rather than a single narrow query.
3. Discovery orchestrates available sources and lanes, prioritising free/low-cost sources before quota-limited or paid providers when suitable.
4. Leads are normalized and deduplicated against the shared database; existing records should be reused where appropriate rather than repeatedly rediscovered.
5. Enrichment runs asynchronously and in parallel where safe, collecting business details and valid contact information. Avoid bad data, e.g. a TikTok/social URL being stored as an email address.
6. Qualification/gating assigns tiers (A/B/C). Tier A should be a smaller, high-quality subset; B adds useful qualified leads; C may broaden coverage but must have at least one usable contact method. Tier proportions should adapt to target size/quality rather than be hardcoded blindly.
7. Qualified leads should be assigned automatically to the correct client/use case. Client A and client B may target different industries even when they serve overlapping end markets; avoid indiscriminate exclusivity. Exclusivity should be policy-driven and appropriate to the client's plan/use case.
8. Once the target number is met, stop the run promptly. Do not continue wasting provider quotas or compute.
9. Assigned leads should be delivered consistently; “assigned”, “reserved”, and “delivered” counts/statuses must reconcile and update accurately.
10. Pipeline events and state should update in the database and frontend in near real time. UI should show discovery, enrichment, deduplication, qualification, assignment, and delivery progress accurately.
11. Client “request opportunity” and automation/growth workflows must work end-to-end, not just look functional.

## Discovery architecture / expectations
- The user previously referenced 1,000+ discovery lanes and 31 agents. These counts were discussed as intended/existing architecture, but **do not assume every lane or agent is active or healthy without verifying the code and execution logs**.
- Discovery sources mentioned across the project include Google/Maps/Places, OSM, web search, Serper, Firecrawl, Scrapling, Crawl4AI, LinkedIn, Instagram, Facebook, Yelp, and other directory/social routes.
- The user wants smart rotation/orchestration across lanes and sources, not just an “exhaustive discovery” mode.
- Exhaustive discovery was intended to be disabled or deprioritised for now because provider quotas are limited. Prefer sensible staged discovery and reuse/deduplication.
- Free/low-cost lanes should generally run before quota-limited/paid lanes, with explicit budgets, per-source limits, fallback handling, and stop conditions.
- Never claim all lanes/agents work merely because definitions exist. Verify executable registration, actual invocation, result handling, error logging, and tests.
- Ensure discovery/enrichment/deduplication can overlap asynchronously when dependencies allow, without races or duplicate assignment.

## Reliability and UX requirements from the session
The user has repeatedly reported:
- Docker Compose API/worker/Redis issues and pipelines stuck in `running`.
- Pipeline requests failing with “LeadX is temporarily unavailable”.
- Small targets (e.g. 5 leads) taking too long.
- Leads passing a gate but not being assigned.
- Assigned leads not matching delivered counts; client lead counts failing to update.
- Client/admin login failures, unexpected logout/session persistence issues.
- Client “request opportunity” and automation workflows not working.
- Dashboard/UI not updating in real time.
- Frontend generic styling, white screens, layout/scroll issues, and inconsistent pages.
- Incorrect enrichment data, including social URLs incorrectly used as email addresses.
These are reported historical issues, not all confirmed still open. Check current code/logs before making assumptions.

UI direction:
- Professional, distinctive enterprise SaaS visual system; avoid generic AI-generated-looking gradients/theme.
- Consistent typography, spacing, branded dark/light palettes, clear data-dense tables/cards, sticky sidebar with main content scrolling, good loading/error/empty states.
- All buttons/actions should have real backend effects and visible status updates.
- Client and admin views should be consistent and usable.

## Database / migrations / security
- Supabase/Postgres is used, with Row Level Security and migrations.
- Earlier migration attempts had duplicate policy errors (`policy ... already exists`) and a `pipeline_runs` status check-constraint failure caused by existing rows; migrations should be idempotent and existing data must be handled safely.
- Do not blindly rerun full migrations or destructive SQL. Inspect current schema and migration history first.
- Authentication and tenant isolation are important; verify client/admin role handling, session refresh, RLS, and service-role usage.
- Do not put service-role keys, Supabase secret keys, AWS keys, JWT secrets, or real `.env` values in this file or in prompts. Use `.env.example` with placeholders.
- The exported chat may contain credentials pasted in historical messages. Treat the ZIP and transcript as sensitive. Rotate any real credentials that were exposed in the chat/export, then update local `.env` securely. Do not repeat or copy those values.

## SES / client email-domain work — latest task
Latest substantive task before the old session broke:
- User requested a minimal SES email send/receive smoke test using `vansh1998singh@gmail.com`, and asked how clients can connect their own email domain. They plan to run the product on a VPS.
- A smoke-test script was reported created at:
  `sqlfix_latest/scripts/test_ses_email.py`
- The reported test command was:
  ```powershell
  python scripts/test_ses_email.py `
    --send `
    --from-email vansh1998singh@gmail.com `
    --to vansh1998singh@gmail.com
  ```
- The reported result was an AWS SES `UnrecognizedClientException`: “The security token included in the request is invalid.” Therefore, **no email was sent** in that test.
- The assistant's reported next step was to use valid AWS credentials in `sqlfix_latest/.env`, verify the sender identity in SES, and if SES remains in sandbox verify the recipient too. Do not expose credentials in chat.
- Sending email and receiving replies are separate systems. SES send API does not automatically read an inbox. Receiving requires either a mailbox provider with IMAP/API or configured SES inbound receipt rules and an S3/SNS/Lambda or equivalent processing flow.
- User asked how to add a domain for VPS deployment. Intended per-client flow discussed in the screenshot:
  1. Client enters their sending domain and sender details.
  2. LeadX creates/verifies the SES identity in the configured AWS account.
  3. LeadX displays required DNS records, including DKIM CNAME records (and any required SPF/DMARC/MX records for the chosen mail architecture).
  4. Client adds DNS records at their domain provider.
  5. LeadX polls/checks SES verification status and lets the client trigger a check.
  6. Only after verification should LeadX send using that client's verified domain/sender; before verification, do not silently send as the client's domain.
- This flow is a desired/previously described plan; verify whether each step is actually implemented before claiming it is done.
- For VPS domain deployment, separately configure app DNS (A/AAAA/CNAME), reverse proxy/TLS, production environment variables, secure secrets, health checks, and background worker/Redis. Email-domain DNS is distinct from the web app's domain DNS.

## Immediate next steps for the new Kiro session
1. Inspect `git status --short` and the relevant `sqlfix_latest` files only.
2. Inspect `sqlfix_latest/scripts/test_ses_email.py`, SES configuration/services, and `.env.example` (never print `.env` values).
3. Confirm whether the smoke test script exists and how it reads AWS config; validate syntax and safe error handling without attempting a real send until valid credentials are configured.
4. Fix the SES invalid-token problem only after the user updates credentials securely. Never ask the user to paste secrets into chat.
5. Trace the client-domain setup UI/API/database path and compare it to the intended flow above. Report gaps before making a minimal patch.
6. Add focused tests for domain identity creation, DNS-record response, verification status, and the “do not send from unverified client domain” guard.
7. Keep credit usage minimal and do not do an unrelated full-system audit unless specifically requested.

## Helpful instruction to start a new Kiro chat
Use this short prompt with the file attached/referenced:

> Read `session-context.md` and treat it as a concise handoff from my previous Kiro session. Inspect current Git status and only the files relevant to the latest task. Continue from the SES smoke test and per-client custom email-domain setup for VPS deployment. Preserve existing architecture and working code. Minimise Kiro credits, tool calls, broad scans, and unnecessary edits. Do not print or request secrets; inspect only `.env.example` and code, never dump `.env`. First report the exact current state and smallest next step, then implement only the necessary change and run targeted tests.
