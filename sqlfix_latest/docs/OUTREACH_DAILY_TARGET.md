# Daily lead target and AI outreach

What it does once a campaign is active:

1. Each day the worker runs discovery for the campaign's ICP until the **daily lead target** (50, 100, 500 or a custom number) is met. Each run asks only for the shortfall, re-runs every `CAMPAIGN_RETICK_MINUTES` (15) while it is still finding leads, and stops until the next day once the target is met. If a run finds nothing, it waits `schedule_hours` before trying again.
2. New leads with a `valid` or `probable` email are enrolled in the campaign sequence. Other leads are never emailed.
3. OpenAI (`OPENAI_API_KEY`, `OPENAI_MODEL`, default `gpt-4o-mini`) writes each email. It may use only the lead's own data and your product description. It must not invent facts. The tokens used and the estimated cost go to the economic ledger (`event_type=provider_usage`, `unit=usd_cents`, `source_type=ai_copy`).
4. **Approval mode is on by default.** Drafts appear under the campaign on the Autonomous growth page. Approve or Reject each one. Turn on "Auto-send AI emails" only after you trust the drafts.
5. Sending rules: at most 3 follow-ups, at least 3 days apart. Recipients are emailed only during business hours in their own timezone, with a random gap between sends. Each mailbox sends at most 30 emails a day by default (`SMTP_DAILY_LIMIT`, `DOMAIN_DAILY_CAP`). Every email carries your sender name, your postal address, an unsubscribe link, and the `List-Unsubscribe` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click` headers.
6. Stop rules: when a lead replies, the sequence stops and you get a notification (in-app, plus Telegram if that is your delivery channel). If the reply asks to be removed ("unsubscribe", "remove me", "stop emailing me"), or the email bounces, or the recipient clicks unsubscribe, the address goes on the global opt-out list and is never emailed again by any campaign.
7. EU and UK recipients are skipped unless you untick "Exclude EU/UK recipients" and write your lawful basis.

## Upgrade note: lower default send caps

The default daily send cap fell from 200 (`SMTP_DAILY_LIMIT`) and 300 (`DOMAIN_DAILY_CAP`) to **30** each. It applies to every existing deployment, not only new mailboxes. To keep the old volume, set both variables explicitly in `.env` before deploying.

## Reply and bounce polling (IMAP)

Only hard bounces (`Action: failed` with `Status: 5.x.x`) that quote a Message-ID we sent to that same address add a global opt-out. Delay notices, soft failures and read receipts are ignored. Mail that matches no send is marked read so unrelated unread mail cannot block the poller. A reply whose handling fails stays unread and is retried.

## Known limits

- EU/UK exclusion uses the lead's country (code or full name). If the country is unknown it checks only the email domain's country suffix, so a `.com` business in the EU is not excluded.
- Pending AI drafts have no expiry or alert. Review them regularly, or leave "Auto-send AI emails" off and pause the campaign if the queue grows.

## Setup (PowerShell)

Run each line on its own.

```powershell
cd C:\path\to\Leadx\sqlfix_latest
git pull
notepad .env
```

Set these values in `.env`. Never commit this file.

```
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
SMTP_HOST=smtp.yourprovider.com
SMTP_PORT=587
SMTP_USER=you@yourdomain.com
SMTP_PASSWORD=app-password
SMTP_TLS=1
SMTP_FROM=Your Name <you@yourdomain.com>
EMAIL_PROVIDER=smtp
SMTP_DAILY_LIMIT=30
SENDER_NAME=Your Company
SENDER_POSTAL_ADDRESS=Street, City, State ZIP, Country
PRODUCT_DESCRIPTION=One or two plain sentences about what you sell and to whom
UNSUBSCRIBE_SECRET=<random string of 32+ characters>
PUBLIC_API_URL=https://your-public-api-url
IMAP_HOST=imap.yourprovider.com
IMAP_PORT=993
IMAP_USER=you@yourdomain.com
IMAP_PASSWORD=app-password
IMAP_FOLDER=INBOX
```

`PUBLIC_API_URL` must be reachable from the internet, or unsubscribe links will not work.

Apply the new migration in the Supabase SQL editor. Open each file, paste it, and run it. Apply any you have not run yet, in this order:

1. `supabase/migrations/20261120000100_leadx_outreach_guardrails.sql`

Rebuild and start:

```powershell
docker compose up --build -d
docker compose logs -f worker
```

## In the portal

1. Open **Admin, Autonomous growth**.
2. Fill in **Sender identity**: name, postal address, and what you sell. Click **Save sender profile**. Nothing is sent while any of these is empty.
3. Create a campaign: choose an ICP, click 50, 100 or 500 for the daily lead target, and leave "Auto-send AI emails" off.
4. Click **Activate**. Drafts start appearing under the campaign. Approve the good ones.
5. Read replies on the Replies page. The notification bell shows each new reply.

## What was only tested with mocks

OpenAI, SMTP, SES, Resend, Brevo, IMAP and Supabase were not called for real. Tests use fakes for each of them (`tests/test_outreach_daily_target.py`). Send one email to yourself in approval mode before you turn on auto-send.
