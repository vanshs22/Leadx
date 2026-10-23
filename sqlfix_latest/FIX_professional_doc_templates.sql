-- Professional multi-design document templates for LeadX
-- Safe on existing DB: UPDATE originals + INSERT new design variants
BEGIN;

UPDATE public.doc_templates
SET body_html = '<!DOCTYPE html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/></head>
<body style="margin:0;padding:0;background:#e8eef5;font-family:''Segoe UI'',Inter,Helvetica,Arial,sans-serif;color:#0f172a;-webkit-font-smoothing:antialiased;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#e8eef5;padding:40px 16px;">
<tr><td align="center">
<table role="presentation" width="680" cellspacing="0" cellpadding="0" style="max-width:680px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 12px 40px rgba(15,23,42,0.12);">
<tr><td style="background:linear-gradient(135deg,#4F46E5 0%,#1e1b4b 100%);padding:32px 40px;">
  <table width="100%"><tr>
    <td><div style="font-size:10px;letter-spacing:0.16em;text-transform:uppercase;color:rgba(255,255,255,0.7);font-weight:700;">CONTRACT</div>
    <div style="font-size:26px;font-weight:800;color:#fff;margin-top:8px;letter-spacing:-0.02em;">Service Agreement</div></td>
    <td align="right" style="vertical-align:top;"><div style="width:40px;height:40px;border-radius:10px;background:rgba(255,255,255,0.15);line-height:40px;text-align:center;color:#fff;font-weight:800;font-size:14px;">LX</div></td>
  </tr></table>
</td></tr>
<tr><td style="padding:40px;">
<p style="margin:0 0 24px;font-size:14px;line-height:1.7;color:#475569;">This Service Agreement is entered into between the parties below as of the date of signature.</p>
<table width="100%" style="margin-bottom:28px;border-collapse:separate;border-spacing:0 0;">
<tr>
<td width="48%" style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:20px;vertical-align:top;">
  <div style="font-size:10px;font-weight:700;letter-spacing:0.1em;color:#6366f1;text-transform:uppercase;">Provider</div>
  <div style="font-size:18px;font-weight:800;margin-top:6px;color:#0f172a;">{{provider_name}}</div>
</td>
<td width="4%"></td>
<td width="48%" style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:20px;vertical-align:top;">
  <div style="font-size:10px;font-weight:700;letter-spacing:0.1em;color:#6366f1;text-transform:uppercase;">Client</div>
  <div style="font-size:18px;font-weight:800;margin-top:6px;color:#0f172a;">{{client_name}}</div>
</td>
</tr>
</table>
<div style="margin-bottom:24px;">
  <div style="font-size:11px;font-weight:800;color:#4F46E5;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:8px;">1 · Scope</div>
  <p style="margin:0;font-size:14px;line-height:1.7;color:#334155;">{{scope}}</p>
</div>
<table width="100%" style="margin-bottom:28px;background:linear-gradient(135deg,#eef2ff,#e0e7ff);border-radius:12px;">
<tr>
<td style="padding:20px;width:50%;"><div style="font-size:10px;font-weight:700;color:#6366f1;">FEE</div><div style="font-size:22px;font-weight:800;color:#312e81;">{{amount}} <span style="font-size:13px;">{{currency}}</span></div></td>
<td style="padding:20px;width:50%;"><div style="font-size:10px;font-weight:700;color:#6366f1;">TERM</div><div style="font-size:22px;font-weight:800;color:#312e81;">{{term}}</div></td>
</tr>
</table>
<p style="margin:0 0 28px;font-size:13px;line-height:1.65;color:#64748b;">Confidential information will not be disclosed except as needed to perform this Agreement. This document is the entire agreement on its subject matter.</p>
<table width="100%"><tr>
<td width="48%" style="padding-top:20px;border-top:2px solid #e2e8f0;vertical-align:top;">
  <div style="font-size:11px;color:#94a3b8;margin-bottom:32px;">Provider signature</div>
  <div style="border-bottom:1px solid #cbd5e1;margin-bottom:8px;"></div>
  <div style="font-size:13px;font-weight:700;">{{provider_name}}</div>
</td>
<td width="4%"></td>
<td width="48%" style="padding-top:20px;border-top:2px solid #e2e8f0;vertical-align:top;">
  <div style="font-size:11px;color:#94a3b8;margin-bottom:32px;">Client signature</div>
  <div style="border-bottom:1px solid #cbd5e1;margin-bottom:8px;"></div>
  <div style="font-size:13px;font-weight:700;">{{client_name}}</div>
</td>
</tr></table>
</td></tr>
<tr><td style="background:#f8fafc;padding:18px 40px;border-top:1px solid #e2e8f0;">
  <table width="100%"><tr>
    <td style="font-size:11px;color:#94a3b8;">LeadX · Confidential</td>
    <td align="right" style="font-size:11px;color:#94a3b8;">{provider_name}</td>
  </tr></table>
</td></tr>
</table></td></tr></table>
</body></html>',
    body_text = 'Service Agreement between {{provider_name}} and {{client_name}}. Scope: {{scope}}. Fee: {{amount}} {{currency}}. Term: {{term}}.',
    description = 'Professional modern service agreement'
WHERE name = 'Service Agreement (simple)' AND tenant_id IS NULL;

UPDATE public.doc_templates
SET body_html = '<!DOCTYPE html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/></head>
<body style="margin:0;padding:0;background:#e8eef5;font-family:''Segoe UI'',Inter,Helvetica,Arial,sans-serif;color:#0f172a;-webkit-font-smoothing:antialiased;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#e8eef5;padding:40px 16px;">
<tr><td align="center">
<table role="presentation" width="680" cellspacing="0" cellpadding="0" style="max-width:680px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 12px 40px rgba(15,23,42,0.12);">
<tr><td style="background:linear-gradient(135deg,#0ea5e9 0%,#1e1b4b 100%);padding:32px 40px;">
  <table width="100%"><tr>
    <td><div style="font-size:10px;letter-spacing:0.16em;text-transform:uppercase;color:rgba(255,255,255,0.7);font-weight:700;">NDA</div>
    <div style="font-size:26px;font-weight:800;color:#fff;margin-top:8px;letter-spacing:-0.02em;">Mutual Non-Disclosure Agreement</div></td>
    <td align="right" style="vertical-align:top;"><div style="width:40px;height:40px;border-radius:10px;background:rgba(255,255,255,0.15);line-height:40px;text-align:center;color:#fff;font-weight:800;font-size:14px;">LX</div></td>
  </tr></table>
</td></tr>
<tr><td style="padding:40px;">
<p style="margin:0 0 20px;font-size:14px;line-height:1.7;color:#334155;">Between <strong>{{party_a}}</strong> and <strong>{{party_b}}</strong>.</p>
<div style="background:#f0f9ff;border-left:4px solid #0ea5e9;padding:18px 20px;border-radius:0 12px 12px 0;margin-bottom:28px;">
  <div style="font-size:10px;font-weight:800;color:#0369a1;letter-spacing:0.1em;">CONFIDENTIALITY PERIOD</div>
  <div style="font-size:20px;font-weight:800;color:#0c4a6e;margin-top:4px;">{{term}}</div>
</div>
<p style="margin:0 0 14px;font-size:14px;line-height:1.7;color:#334155;"><strong>1. Confidential Information</strong> means non-public business, technical, or financial information disclosed by either party.</p>
<p style="margin:0 0 14px;font-size:14px;line-height:1.7;color:#334155;"><strong>2. Obligations.</strong> Use only to evaluate or perform a business relationship; do not disclose to third parties without written consent.</p>
<p style="margin:0 0 14px;font-size:14px;line-height:1.7;color:#334155;"><strong>3. Exclusions.</strong> Does not apply to public information, independent development, or rightful third-party receipt without duty of confidentiality.</p>
<p style="margin:0 0 28px;font-size:14px;line-height:1.7;color:#334155;"><strong>4. Term.</strong> Obligations continue for the period stated above from each disclosure.</p>
<table width="100%"><tr>
<td width="48%" style="padding-top:16px;border-top:2px solid #e2e8f0;"><div style="font-size:11px;color:#94a3b8;margin-bottom:28px;">Party A</div><div style="border-bottom:1px solid #cbd5e1;margin-bottom:8px;"></div><div style="font-weight:700;">{{party_a}}</div></td>
<td width="4%"></td>
<td width="48%" style="padding-top:16px;border-top:2px solid #e2e8f0;"><div style="font-size:11px;color:#94a3b8;margin-bottom:28px;">Party B</div><div style="border-bottom:1px solid #cbd5e1;margin-bottom:8px;"></div><div style="font-weight:700;">{{party_b}}</div></td>
</tr></table>
</td></tr>
<tr><td style="background:#f8fafc;padding:18px 40px;border-top:1px solid #e2e8f0;">
  <table width="100%"><tr>
    <td style="font-size:11px;color:#94a3b8;">LeadX · Confidential</td>
    <td align="right" style="font-size:11px;color:#94a3b8;">{provider_name}</td>
  </tr></table>
</td></tr>
</table></td></tr></table>
</body></html>',
    body_text = 'Mutual NDA between {{party_a}} and {{party_b}} for {{term}}.',
    description = 'Professional mutual NDA'
WHERE name = 'Mutual NDA' AND tenant_id IS NULL;

UPDATE public.doc_templates
SET body_html = '<!DOCTYPE html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/></head>
<body style="margin:0;padding:0;background:#e8eef5;font-family:''Segoe UI'',Inter,Helvetica,Arial,sans-serif;color:#0f172a;-webkit-font-smoothing:antialiased;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#e8eef5;padding:40px 16px;">
<tr><td align="center">
<table role="presentation" width="680" cellspacing="0" cellpadding="0" style="max-width:680px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 12px 40px rgba(15,23,42,0.12);">
<tr><td style="background:linear-gradient(135deg,#059669 0%,#1e1b4b 100%);padding:32px 40px;">
  <table width="100%"><tr>
    <td><div style="font-size:10px;letter-spacing:0.16em;text-transform:uppercase;color:rgba(255,255,255,0.7);font-weight:700;">INVOICE</div>
    <div style="font-size:26px;font-weight:800;color:#fff;margin-top:8px;letter-spacing:-0.02em;">Invoice</div></td>
    <td align="right" style="vertical-align:top;"><div style="width:40px;height:40px;border-radius:10px;background:rgba(255,255,255,0.15);line-height:40px;text-align:center;color:#fff;font-weight:800;font-size:14px;">LX</div></td>
  </tr></table>
</td></tr>
<tr><td style="padding:40px;">
<table width="100%" style="margin-bottom:32px;"><tr>
<td style="vertical-align:top;">
  <div style="font-size:10px;font-weight:700;letter-spacing:0.12em;color:#64748b;text-transform:uppercase;">Bill to</div>
  <div style="font-size:20px;font-weight:800;margin-top:6px;">{{client_name}}</div>
</td>
<td style="vertical-align:top;text-align:right;">
  <span style="display:inline-block;background:#ecfdf5;color:#047857;font-size:11px;font-weight:800;padding:6px 14px;border-radius:999px;letter-spacing:0.06em;">INVOICE</span>
  <div style="font-size:15px;font-weight:700;margin-top:12px;">#{{invoice_number}}</div>
  <div style="font-size:13px;color:#64748b;margin-top:4px;">Due {{due_date}}</div>
</td>
</tr></table>
<table width="100%" style="border-collapse:collapse;margin-bottom:28px;">
<tr style="background:#f8fafc;">
  <th align="left" style="padding:14px;font-size:10px;letter-spacing:0.08em;text-transform:uppercase;color:#64748b;border-bottom:2px solid #e2e8f0;">Description</th>
  <th align="right" style="padding:14px;font-size:10px;letter-spacing:0.08em;text-transform:uppercase;color:#64748b;border-bottom:2px solid #e2e8f0;">Amount</th>
</tr>
<tr>
  <td style="padding:18px 14px;font-size:14px;color:#334155;border-bottom:1px solid #f1f5f9;">{{line_items}}</td>
  <td align="right" style="padding:18px 14px;font-size:14px;font-weight:700;border-bottom:1px solid #f1f5f9;">{{amount}} {{currency}}</td>
</tr>
</table>
<table width="100%"><tr><td></td>
<td width="240" style="background:#0f172a;border-radius:14px;padding:22px 24px;">
  <div style="font-size:10px;color:#94a3b8;letter-spacing:0.1em;text-transform:uppercase;">Amount due</div>
  <div style="font-size:28px;font-weight:800;color:#fff;margin-top:6px;">{{amount}} <span style="font-size:14px;color:#94a3b8;">{{currency}}</span></div>
</td></tr></table>
</td></tr>
<tr><td style="background:#f8fafc;padding:18px 40px;border-top:1px solid #e2e8f0;">
  <table width="100%"><tr>
    <td style="font-size:11px;color:#94a3b8;">LeadX · Confidential</td>
    <td align="right" style="font-size:11px;color:#94a3b8;">{provider_name}</td>
  </tr></table>
</td></tr>
</table></td></tr></table>
</body></html>',
    body_text = 'Invoice {{invoice_number}} to {{client_name}} for {{amount}} {{currency}}, due {{due_date}}.',
    description = 'Professional invoice'
WHERE name = 'Standard Invoice' AND tenant_id IS NULL;

UPDATE public.doc_templates
SET body_html = '<!DOCTYPE html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/></head>
<body style="margin:0;padding:0;background:#e8eef5;font-family:''Segoe UI'',Inter,Helvetica,Arial,sans-serif;color:#0f172a;-webkit-font-smoothing:antialiased;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#e8eef5;padding:40px 16px;">
<tr><td align="center">
<table role="presentation" width="680" cellspacing="0" cellpadding="0" style="max-width:680px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 12px 40px rgba(15,23,42,0.12);">
<tr><td style="background:linear-gradient(135deg,#7c3aed 0%,#1e1b4b 100%);padding:32px 40px;">
  <table width="100%"><tr>
    <td><div style="font-size:10px;letter-spacing:0.16em;text-transform:uppercase;color:rgba(255,255,255,0.7);font-weight:700;">PROPOSAL</div>
    <div style="font-size:26px;font-weight:800;color:#fff;margin-top:8px;letter-spacing:-0.02em;">Lead Package Proposal</div></td>
    <td align="right" style="vertical-align:top;"><div style="width:40px;height:40px;border-radius:10px;background:rgba(255,255,255,0.15);line-height:40px;text-align:center;color:#fff;font-weight:800;font-size:14px;">LX</div></td>
  </tr></table>
</td></tr>
<tr><td style="padding:40px;">
<p style="margin:0 0 8px;font-size:15px;color:#334155;">Hi <strong>{{client_name}}</strong>,</p>
<p style="margin:0 0 28px;font-size:14px;line-height:1.7;color:#64748b;">Here is a clear package for exclusive lead delivery in your market.</p>
<table width="100%" style="margin-bottom:28px;border-collapse:separate;border-spacing:8px 0;">
<tr>
<td width="32%" style="background:#eef2ff;border-radius:14px;padding:20px;vertical-align:top;">
  <div style="font-size:10px;font-weight:800;color:#6366f1;">LEADS / MO</div>
  <div style="font-size:32px;font-weight:800;color:#312e81;margin-top:4px;">{{lead_count}}</div>
  <div style="font-size:12px;color:#64748b;">Tier A priority</div>
</td>
<td width="32%" style="background:#f0fdf4;border-radius:14px;padding:20px;vertical-align:top;">
  <div style="font-size:10px;font-weight:800;color:#16a34a;">MARKET</div>
  <div style="font-size:18px;font-weight:800;color:#14532d;margin-top:6px;">{{vertical}}</div>
  <div style="font-size:12px;color:#64748b;">{{geo}}</div>
</td>
<td width="32%" style="background:#fff7ed;border-radius:14px;padding:20px;vertical-align:top;">
  <div style="font-size:10px;font-weight:800;color:#ea580c;">INVESTMENT</div>
  <div style="font-size:22px;font-weight:800;color:#9a3412;margin-top:6px;">{{amount}}</div>
  <div style="font-size:12px;color:#64748b;">per month</div>
</td>
</tr>
</table>
<div style="padding:18px 20px;border:1px solid #e2e8f0;border-radius:12px;margin-bottom:24px;">
  <div style="font-size:12px;font-weight:800;color:#0f172a;margin-bottom:6px;">Exclusivity</div>
  <p style="margin:0;font-size:14px;line-height:1.6;color:#475569;">Offer category <strong>{{offer_category}}</strong> in <strong>{{geo}}</strong> — same-category competitors in overlapping territory are blocked under our exclusivity rules.</p>
</div>
<ul style="margin:0;padding-left:20px;font-size:14px;line-height:1.8;color:#334155;">
  <li>Enriched contacts &amp; quality scoring</li>
  <li>CRM delivery &amp; optional sequences</li>
  <li>Portal access for your team</li>
</ul>
</td></tr>
<tr><td style="background:#f8fafc;padding:18px 40px;border-top:1px solid #e2e8f0;">
  <table width="100%"><tr>
    <td style="font-size:11px;color:#94a3b8;">LeadX · Confidential</td>
    <td align="right" style="font-size:11px;color:#94a3b8;">{provider_name}</td>
  </tr></table>
</td></tr>
</table></td></tr></table>
</body></html>',
    body_text = 'Proposal for {{client_name}}: {{lead_count}} Tier A {{vertical}} leads in {{geo}} at {{amount}}/mo.',
    description = 'Professional lead package proposal'
WHERE name = 'Lead Package Proposal' AND tenant_id IS NULL;

UPDATE public.doc_templates
SET body_html = '<!DOCTYPE html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/></head>
<body style="margin:0;padding:0;background:#e8eef5;font-family:''Segoe UI'',Inter,Helvetica,Arial,sans-serif;color:#0f172a;-webkit-font-smoothing:antialiased;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#e8eef5;padding:40px 16px;">
<tr><td align="center">
<table role="presentation" width="680" cellspacing="0" cellpadding="0" style="max-width:680px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 12px 40px rgba(15,23,42,0.12);">
<tr><td style="background:linear-gradient(135deg,#4F46E5 0%,#1e1b4b 100%);padding:32px 40px;">
  <table width="100%"><tr>
    <td><div style="font-size:10px;letter-spacing:0.16em;text-transform:uppercase;color:rgba(255,255,255,0.7);font-weight:700;">ONBOARDING</div>
    <div style="font-size:26px;font-weight:800;color:#fff;margin-top:8px;letter-spacing:-0.02em;">Client Onboarding</div></td>
    <td align="right" style="vertical-align:top;"><div style="width:40px;height:40px;border-radius:10px;background:rgba(255,255,255,0.15);line-height:40px;text-align:center;color:#fff;font-weight:800;font-size:14px;">LX</div></td>
  </tr></table>
</td></tr>
<tr><td style="padding:40px;">
<p style="margin:0 0 8px;font-size:18px;font-weight:800;color:#0f172a;">Welcome, {{client_name}}</p>
<p style="margin:0 0 28px;font-size:14px;line-height:1.65;color:#64748b;">Complete these steps to get the most from your exclusive lead program.</p>
<table width="100%" style="border-collapse:separate;border-spacing:0 12px;">
<tr><td style="padding:16px 18px;border:1px solid #e2e8f0;border-radius:12px;">
  <div style="font-size:10px;font-weight:800;color:#4F46E5;letter-spacing:0.08em;">STEP 1</div>
  <div style="font-size:15px;font-weight:700;margin-top:4px;">Confirm your ICP</div>
  <div style="font-size:13px;color:#64748b;margin-top:4px;">{{vertical}} · {{geo}}</div>
</td></tr>
<tr><td style="padding:16px 18px;border:1px solid #e2e8f0;border-radius:12px;">
  <div style="font-size:10px;font-weight:800;color:#4F46E5;letter-spacing:0.08em;">STEP 2</div>
  <div style="font-size:15px;font-weight:700;margin-top:4px;">Sign in to the CRM portal</div>
  <div style="font-size:13px;color:#64748b;margin-top:4px;">Use the email and password from your administrator.</div>
</td></tr>
<tr><td style="padding:16px 18px;border:1px solid #e2e8f0;border-radius:12px;">
  <div style="font-size:10px;font-weight:800;color:#4F46E5;letter-spacing:0.08em;">STEP 3</div>
  <div style="font-size:15px;font-weight:700;margin-top:4px;">Review your first lead pack</div>
  <div style="font-size:13px;color:#64748b;margin-top:4px;">Check tiers, pitch lines, and contacts before outreach.</div>
</td></tr>
<tr><td style="padding:16px 18px;border:1px solid #e2e8f0;border-radius:12px;">
  <div style="font-size:10px;font-weight:800;color:#4F46E5;letter-spacing:0.08em;">STEP 4</div>
  <div style="font-size:15px;font-weight:700;margin-top:4px;">First pipeline delivery</div>
  <div style="font-size:13px;color:#64748b;margin-top:4px;">Your operator will deliver leads into this workspace.</div>
</td></tr>
</table>
</td></tr>
<tr><td style="background:#f8fafc;padding:18px 40px;border-top:1px solid #e2e8f0;">
  <table width="100%"><tr>
    <td style="font-size:11px;color:#94a3b8;">LeadX · Confidential</td>
    <td align="right" style="font-size:11px;color:#94a3b8;">{provider_name}</td>
  </tr></table>
</td></tr>
</table></td></tr></table>
</body></html>',
    body_text = 'Welcome {{client_name}}. Confirm ICP {{vertical}} / {{geo}}, CRM login, sample pack, first run.',
    description = 'Professional onboarding checklist'
WHERE name = 'Client Onboarding Checklist' AND tenant_id IS NULL;

INSERT INTO public.doc_templates (tenant_id, category, name, description, body_html, body_text, variables, channel_hint, active)
SELECT NULL, 'contract', 'Service Agreement — Classic', 'Minimal classic legal layout', '<!DOCTYPE html>
<html><head><meta charset="utf-8"/></head>
<body style="margin:0;padding:0;background:#fafafa;font-family:Georgia,''Times New Roman'',serif;color:#1a1a1a;">
<table width="100%" style="padding:48px 16px;"><tr><td align="center">
<table width="640" style="max-width:640px;width:100%;background:#fff;border:1px solid #e5e5e5;">
<tr><td style="border-top:6px solid #0f172a;padding:36px 40px 12px;">
  <div style="font-size:11px;letter-spacing:0.2em;text-transform:uppercase;color:#737373;">SERVICE AGREEMENT</div>
  <div style="height:1px;background:#e5e5e5;margin:16px 0 0;"></div>
</td></tr>
<tr><td style="padding:24px 40px 40px;font-size:15px;line-height:1.7;">
<p style="margin:0 0 20px;">This agreement is between <strong>{{provider_name}}</strong> (“Provider”) and <strong>{{client_name}}</strong> (“Client”).</p>
<p style="margin:0 0 12px;"><strong>Scope.</strong> {{scope}}</p>
<p style="margin:0 0 12px;"><strong>Fee.</strong> {{amount}} {{currency}}</p>
<p style="margin:0 0 24px;"><strong>Term.</strong> {{term}}</p>
<p style="margin:0 0 32px;font-size:14px;color:#525252;">Each party agrees to perform in good faith. Confidential information remains protected for the term of this agreement.</p>
<table width="100%"><tr>
<td width="45%"><p style="margin:0 0 40px;font-size:12px;color:#737373;">Provider</p><div style="border-bottom:1px solid #a3a3a3;"></div><p style="margin:8px 0 0;font-size:13px;">{{provider_name}}</p></td>
<td width="10%"></td>
<td width="45%"><p style="margin:0 0 40px;font-size:12px;color:#737373;">Client</p><div style="border-bottom:1px solid #a3a3a3;"></div><p style="margin:8px 0 0;font-size:13px;">{{client_name}}</p></td>
</tr></table>
</td></tr>
</table></td></tr></table>
</body></html>', 'Service Agreement between {{provider_name}} and {{client_name}}. Scope: {{scope}}. Fee: {{amount}} {{currency}}. Term: {{term}}.', '["provider_name","client_name","scope","amount","currency","term"]'::jsonb, 'email', true
WHERE NOT EXISTS (SELECT 1 FROM public.doc_templates WHERE name = 'Service Agreement — Classic' AND tenant_id IS NULL);

INSERT INTO public.doc_templates (tenant_id, category, name, description, body_html, body_text, variables, channel_hint, active)
SELECT NULL, 'proposal', 'Lead Package Proposal — Bold', 'Dark bold proposal style', '<!DOCTYPE html>
<html><head><meta charset="utf-8"/></head>
<body style="margin:0;padding:0;background:#0f0a1a;font-family:Inter,system-ui,sans-serif;color:#e2e8f0;">
<table width="100%" style="padding:40px 16px;background:#0f0a1a;"><tr><td align="center">
<table width="680" style="max-width:680px;background:#1a1225;border-radius:20px;overflow:hidden;border:1px solid #2e2240;">
<tr><td style="padding:28px 36px;background:#7c3aed;">
  <div style="font-size:22px;font-weight:800;color:#fff;">Exclusive Lead Proposal</div>
</td></tr>
<tr><td style="padding:36px;">
<p style="margin:0 0 20px;font-size:15px;color:#c4b5fd;">Prepared for <strong style="color:#fff;">{{client_name}}</strong></p>
<p style="margin:0 0 24px;font-size:14px;line-height:1.7;color:#a78bfa;">{{lead_count}} Tier A {{vertical}} leads in {{geo}} · {{amount}}/mo · exclusivity: {{offer_category}}</p>
<p style="margin:0;font-size:13px;line-height:1.7;color:#94a3b8;">Enriched contacts, quality scoring, CRM portal access, and optional outreach sequences.</p>
</td></tr>
</table></td></tr></table>
</body></html>', 'Proposal for {{client_name}}: {{lead_count}} Tier A {{vertical}} leads in {{geo}} at {{amount}}/mo.', '["client_name","lead_count","vertical","geo","amount","offer_category"]'::jsonb, 'email', true
WHERE NOT EXISTS (SELECT 1 FROM public.doc_templates WHERE name = 'Lead Package Proposal — Bold' AND tenant_id IS NULL);

COMMIT;
