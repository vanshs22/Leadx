"""LeadX 11.0 connector registry.

Connections are tenant-scoped, optionally user-owned, encrypted at rest, and can
use either LeadX-managed OAuth (platform app credentials + user's account) or
customer BYOK credentials. A universal HTTP/webhook adapter keeps the connector
surface extensible without requiring a bespoke adapter for every SaaS tool.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
import httpx
from app.integrations.prod.crypto_config import encrypt_config, decrypt_config
from app.memory.supabase_client import get_supabase

# Native connectors + broad catalog. Provider adapters are deliberately explicit;
# generic_http/webhook cover tools without a native adapter.
PROVIDERS = {
    "gmail": {"name": "Gmail", "purpose": "Email / replies / activity", "auth": "oauth_or_byok", "category": "email"},
    "google_calendar": {"name": "Google Calendar", "purpose": "Calendar / scheduling", "auth": "oauth_or_byok", "category": "productivity"},
    "google_drive": {"name": "Google Drive", "purpose": "Files / documents", "auth": "oauth_or_byok", "category": "productivity"},
    "google_sheets": {"name": "Google Sheets", "purpose": "Spreadsheet sync", "auth": "oauth_or_byok", "category": "data"},
    "outlook": {"name": "Outlook / Microsoft 365", "purpose": "Email / calendar", "auth": "oauth_or_byok", "category": "email"},
    "microsoft_calendar": {"name": "Microsoft Calendar", "purpose": "Calendar / scheduling", "auth": "oauth_or_byok", "category": "productivity"},
    "microsoft_teams": {"name": "Microsoft Teams", "purpose": "Notifications / collaboration", "auth": "oauth_or_byok", "category": "collaboration"},
    "hubspot": {"name": "HubSpot", "purpose": "CRM / marketing sync", "auth": "oauth_or_byok", "category": "crm"},
    "salesforce": {"name": "Salesforce", "purpose": "CRM / revenue sync", "auth": "oauth_or_byok", "category": "crm"},
    "pipedrive": {"name": "Pipedrive", "purpose": "CRM / deals", "auth": "api_key_or_oauth", "category": "crm"},
    "close": {"name": "Close", "purpose": "CRM / sales", "auth": "api_key", "category": "crm"},
    "gohighlevel": {"name": "GoHighLevel", "purpose": "CRM / agency delivery", "auth": "oauth_or_byok", "category": "crm"},
    "slack": {"name": "Slack", "purpose": "Alerts / operations", "auth": "oauth_or_byok", "category": "collaboration"},
    "calcom": {"name": "Cal.com", "purpose": "Scheduling", "auth": "api_key_or_oauth", "category": "scheduling"},
    "notion": {"name": "Notion", "purpose": "Knowledge / records", "auth": "api_key_or_oauth", "category": "productivity"},
    "airtable": {"name": "Airtable", "purpose": "Database / operations", "auth": "api_key_or_oauth", "category": "data"},
    "asana": {"name": "Asana", "purpose": "Tasks / projects", "auth": "api_key_or_oauth", "category": "productivity"},
    "clickup": {"name": "ClickUp", "purpose": "Tasks / projects", "auth": "api_key", "category": "productivity"},
    "linear": {"name": "Linear", "purpose": "Issues / engineering", "auth": "api_key", "category": "productivity"},
    "zapier": {"name": "Zapier", "purpose": "Automation", "auth": "webhook", "category": "automation"},
    "make": {"name": "Make", "purpose": "Automation", "auth": "webhook", "category": "automation"},
    "n8n": {"name": "n8n", "purpose": "Automation", "auth": "webhook", "category": "automation"},
    "webhook": {"name": "Webhooks", "purpose": "Universal event delivery", "auth": "secret", "category": "developer"},
    "generic_http": {"name": "HTTP API / BYOK", "purpose": "Connect any API you control", "auth": "byok", "category": "developer"},
}


def _sb():
    return get_supabase()


def list_providers() -> list[dict]:
    return [{"provider": k, **v} for k, v in PROVIDERS.items()]


def list_connections(tenant_id: str) -> list[dict]:
    rows = (_sb().table("integration_connections")
        .select("id,provider,status,external_account_id,scopes,metadata,last_sync_at,last_error,created_at,updated_at,auth_mode,connection_name,owner_user_id")
        .eq("tenant_id", tenant_id).order("created_at", desc=True).execute().data or [])
    # Presentation-safe contract: never expose encrypted secrets, provider IDs,
    # OAuth metadata, internal user IDs, or raw provider responses to the portal.
    safe=[]
    for row in rows:
        safe.append({
            "id": row.get("id"), "provider": row.get("provider"), "status": row.get("status"),
            "connection_name": row.get("connection_name") or row.get("provider"),
            "auth_mode": row.get("auth_mode") or "platform",
            "last_sync_at": row.get("last_sync_at"), "last_error": row.get("last_error"),
            "created_at": row.get("created_at"), "updated_at": row.get("updated_at"),
            "has_credentials": True,
        })
    return safe


def save_connection(
    tenant_id: str,
    provider: str,
    config: dict,
    metadata: dict | None = None,
    *,
    owner_user_id: str | None = None,
    connection_name: str | None = None,
    auth_mode: str | None = None,
    external_account_id: str | None = None,
    scopes: list[str] | None = None,
) -> dict:
    if provider not in PROVIDERS:
        raise ValueError("Unsupported connector")
    if not isinstance(config, dict):
        raise ValueError("connector config must be an object")
    auth_mode = auth_mode or ("byok" if any(k in config for k in ("api_key", "access_token", "client_secret")) else "platform")
    if auth_mode not in {"platform", "byok"}:
        raise ValueError("invalid auth mode")
    blob = encrypt_config(config)
    q = _sb().table("integration_connections").select("id") .eq("tenant_id", tenant_id).eq("provider", provider)
    if external_account_id:
        q = q.eq("external_account_id", external_account_id)
    elif connection_name:
        q = q.eq("connection_name", connection_name)
    existing = q.limit(1).execute().data or []
    payload = {
        "tenant_id": tenant_id, "provider": provider, "status": "connected", "secret_ref": blob,
        "metadata": metadata or {}, "updated_at": datetime.now(timezone.utc).isoformat(),
        "auth_mode": auth_mode, "connection_name": connection_name or PROVIDERS[provider]["name"],
        "owner_user_id": owner_user_id, "external_account_id": external_account_id,
        "scopes": scopes or [],
    }
    if existing:
        row = _sb().table("integration_connections").update(payload).eq("id", existing[0]["id"]).execute().data or []
    else:
        row = _sb().table("integration_connections").insert(payload).execute().data or []
    return {k: v for k, v in (row[0] if row else {"provider": provider, "status": "connected"}).items() if k != "secret_ref"}



def select_connection(tenant_id: str, provider: str, *, connection_id: str | None = None, owner_user_id: str | None = None) -> dict | None:
    """Resolve one tenant connection deterministically; never pick another tenant's account."""
    q = _sb().table("integration_connections").select("*").eq("tenant_id", tenant_id).eq("provider", provider).eq("status", "connected")
    if connection_id:
        q = q.eq("id", connection_id)
    elif owner_user_id:
        q = q.eq("owner_user_id", owner_user_id)
    rows = q.order("updated_at", desc=True).limit(1).execute().data or []
    return rows[0] if rows else None

def resolve_connection(
    tenant_id: str,
    provider: str,
    *,
    connection_id: str | None = None,
    owner_user_id: str | None = None,
) -> dict | None:
    """Resolve a tenant connector while preserving disabled/unconfigured state.

    ``select_connection`` remains the compatibility helper for callers that only
    want a connected row.  Delivery uses this variant so it can explicitly
    decline disabled connectors instead of silently treating them as missing.
    """
    q = _sb().table("integration_connections").select("*").eq("tenant_id", tenant_id).eq("provider", provider)
    if connection_id:
        q = q.eq("id", connection_id)
    elif owner_user_id:
        q = q.eq("owner_user_id", owner_user_id)
    rows = q.order("updated_at", desc=True).limit(20).execute().data or []
    connected = [row for row in rows if str(row.get("status") or "").lower() == "connected"]
    return (connected or rows or [None])[0]


def _config(row: dict) -> dict:
    blob = row.get("secret_ref")
    if not blob:
        return {}
    return decrypt_config(blob)


async def test_connection(row: dict) -> dict:
    provider = row.get("provider")
    try:
        cfg = _config(row)
        auth = cfg.get("access_token") or cfg.get("api_key")
        async with httpx.AsyncClient(timeout=12, follow_redirects=False) as c:
            if provider == "google_calendar":
                r = await c.get("https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=1", headers={"Authorization": f"Bearer {auth}"})
            elif provider == "gmail":
                r = await c.get("https://gmail.googleapis.com/gmail/v1/users/me/profile", headers={"Authorization": f"Bearer {auth}"})
            elif provider == "google_drive":
                r = await c.get("https://www.googleapis.com/drive/v3/about?fields=user", headers={"Authorization": f"Bearer {auth}"})
            elif provider == "google_sheets":
                r = await c.get("https://sheets.googleapis.com/v4/spreadsheets", headers={"Authorization": f"Bearer {auth}"})
            elif provider in ("outlook", "microsoft_calendar", "microsoft_teams"):
                endpoint = {"outlook":"https://graph.microsoft.com/v1.0/me", "microsoft_calendar":"https://graph.microsoft.com/v1.0/me/calendar", "microsoft_teams":"https://graph.microsoft.com/v1.0/me/joinedTeams"}[provider]
                r = await c.get(endpoint, headers={"Authorization": f"Bearer {auth}"})
            elif provider == "slack":
                r = await c.get("https://slack.com/api/auth.test", headers={"Authorization": f"Bearer {auth}"})
            elif provider == "hubspot":
                r = await c.get("https://api.hubapi.com/crm/v3/objects/companies?limit=1", headers={"Authorization": f"Bearer {auth}"})
            elif provider == "salesforce":
                base = (cfg.get("instance_url") or "").rstrip("/")
                if not base: return {"ok": False, "error": "Salesforce instance URL is required"}
                r = await c.get(f"{base}/services/data/", headers={"Authorization": f"Bearer {cfg.get('access_token')}"})
            elif provider == "pipedrive":
                r = await c.get("https://api.pipedrive.com/v1/users/me", params={"api_token": cfg["api_key"]})
            elif provider == "close":
                r = await c.get("https://api.close.com/api/v1/me/", auth=(cfg["api_key"], ""))
            elif provider == "gohighlevel":
                location_id = str(cfg.get("location_id") or "").strip()
                if not location_id or not cfg.get("access_token"):
                    return {"ok": False, "error": "GoHighLevel location and access token are required"}
                r = await c.get(
                    f"https://services.leadconnectorhq.com/locations/{location_id}",
                    headers={"Authorization": f"Bearer {cfg['access_token']}", "Version": "2021-07-28"},
                )
            elif provider == "calcom":
                r = await c.get("https://api.cal.com/v1/me", headers={"Authorization": f"Bearer {cfg['api_key']}"})
            elif provider == "clickup":
                r = await c.get("https://api.clickup.com/api/v2/user", headers={"Authorization": cfg["api_key"]})
            elif provider == "linear":
                r = await c.post("https://api.linear.app/graphql", headers={"Authorization": cfg["api_key"]}, json={"query":"query { viewer { id } }"})
            elif provider == "notion":
                r = await c.get("https://api.notion.com/v1/users/me", headers={"Authorization": f"Bearer {cfg['api_key']}", "Notion-Version":"2022-06-28"})
            elif provider == "airtable":
                r = await c.get("https://api.airtable.com/v0/meta/whoami", headers={"Authorization": f"Bearer {cfg['api_key']}"})
            elif provider == "asana":
                r = await c.get("https://app.asana.com/api/1.0/users/me", headers={"Authorization": f"Bearer {cfg['api_key']}"})
            elif provider in ("webhook", "zapier", "make", "n8n", "generic_http"):
                url = str(cfg.get("url") or cfg.get("health_url") or "").strip()
                if not url: return {"ok": False, "error": "URL is required"}
                from app.integrations.prod.net_safety import assert_public_url
                reason = await assert_public_url(url)
                if reason: return {"ok": False, "error": f"unsafe connector URL: {reason}"}
                headers = cfg.get("headers") if isinstance(cfg.get("headers"), dict) else {}
                r = await c.get(url, headers=headers)
            else:
                return {"ok": False, "error": "Provider adapter not configured"}
        ok = r.status_code < 400
        return {"ok": ok, "status_code": r.status_code, "message": "Connection healthy" if ok else "Connection failed"}
    except Exception as e:
        from app.integrations.prod.crm_delivery import redact_error
        return {"ok": False, "error": redact_error(e) or "connection test failed"}


OAUTH_PROVIDERS = {
    "google_calendar": {"authorize":"https://accounts.google.com/o/oauth2/v2/auth","token":"https://oauth2.googleapis.com/token","client_id_env":"GOOGLE_OAUTH_CLIENT_ID","client_secret_env":"GOOGLE_OAUTH_CLIENT_SECRET","redirect_env":"GOOGLE_OAUTH_REDIRECT_URI","scope":"https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/calendar.readonly","access_type":"offline","prompt":"consent"},
    "gmail": {"authorize":"https://accounts.google.com/o/oauth2/v2/auth","token":"https://oauth2.googleapis.com/token","client_id_env":"GOOGLE_OAUTH_CLIENT_ID","client_secret_env":"GOOGLE_OAUTH_CLIENT_SECRET","redirect_env":"GOOGLE_OAUTH_REDIRECT_URI","scope":"https://www.googleapis.com/auth/gmail.modify","access_type":"offline","prompt":"consent"},
    "google_drive": {"authorize":"https://accounts.google.com/o/oauth2/v2/auth","token":"https://oauth2.googleapis.com/token","client_id_env":"GOOGLE_OAUTH_CLIENT_ID","client_secret_env":"GOOGLE_OAUTH_CLIENT_SECRET","redirect_env":"GOOGLE_OAUTH_REDIRECT_URI","scope":"https://www.googleapis.com/auth/drive.readonly","access_type":"offline","prompt":"consent"},
    "google_sheets": {"authorize":"https://accounts.google.com/o/oauth2/v2/auth","token":"https://oauth2.googleapis.com/token","client_id_env":"GOOGLE_OAUTH_CLIENT_ID","client_secret_env":"GOOGLE_OAUTH_CLIENT_SECRET","redirect_env":"GOOGLE_OAUTH_REDIRECT_URI","scope":"https://www.googleapis.com/auth/spreadsheets","access_type":"offline","prompt":"consent"},
    "outlook": {"authorize":"https://login.microsoftonline.com/common/oauth2/v2.0/authorize","token":"https://login.microsoftonline.com/common/oauth2/v2.0/token","client_id_env":"MICROSOFT_CLIENT_ID","client_secret_env":"MICROSOFT_CLIENT_SECRET","redirect_env":"MICROSOFT_OAUTH_REDIRECT_URI","scope":"offline_access openid profile email Mail.ReadWrite Calendars.ReadWrite","response_mode":"query"},
    "microsoft_calendar": {"authorize":"https://login.microsoftonline.com/common/oauth2/v2.0/authorize","token":"https://login.microsoftonline.com/common/oauth2/v2.0/token","client_id_env":"MICROSOFT_CLIENT_ID","client_secret_env":"MICROSOFT_CLIENT_SECRET","redirect_env":"MICROSOFT_OAUTH_REDIRECT_URI","scope":"offline_access openid profile email Calendars.ReadWrite","response_mode":"query"},
    "microsoft_teams": {"authorize":"https://login.microsoftonline.com/common/oauth2/v2.0/authorize","token":"https://login.microsoftonline.com/common/oauth2/v2.0/token","client_id_env":"MICROSOFT_CLIENT_ID","client_secret_env":"MICROSOFT_CLIENT_SECRET","redirect_env":"MICROSOFT_OAUTH_REDIRECT_URI","scope":"offline_access openid profile email Team.ReadBasic.All","response_mode":"query"},
    "hubspot": {"authorize":"https://app.hubspot.com/oauth/authorize","token":"https://api.hubapi.com/oauth/v1/token","client_id_env":"HUBSPOT_CLIENT_ID","client_secret_env":"HUBSPOT_CLIENT_SECRET","redirect_env":"HUBSPOT_OAUTH_REDIRECT_URI","scope":"crm.objects.contacts.read crm.objects.contacts.write crm.objects.companies.read crm.objects.companies.write crm.objects.deals.read crm.objects.deals.write"},
    "salesforce": {"authorize":"https://login.salesforce.com/services/oauth2/authorize","token":"https://login.salesforce.com/services/oauth2/token","client_id_env":"SALESFORCE_CLIENT_ID","client_secret_env":"SALESFORCE_CLIENT_SECRET","redirect_env":"SALESFORCE_OAUTH_REDIRECT_URI","scope":"api refresh_token offline_access"},
    "gohighlevel": {"authorize":"https://marketplace.gohighlevel.com/oauth/chooselocation","token":"https://services.leadconnectorhq.com/oauth/token","client_id_env":"GHL_CLIENT_ID","client_secret_env":"GHL_CLIENT_SECRET","redirect_env":"GHL_OAUTH_REDIRECT_URI","scope":"contacts.readonly contacts.write opportunities.readonly opportunities.write calendars.readonly calendars.write"},
}


def oauth_config(provider: str) -> dict:
    meta = OAUTH_PROVIDERS.get(provider)
    if not meta:
        raise ValueError("OAuth is not configured for this provider")
    client_id = os.getenv(meta["client_id_env"], "")
    client_secret = os.getenv(meta["client_secret_env"], "")
    redirect = os.getenv(meta["redirect_env"], "")
    if not client_id or not client_secret or not redirect:
        raise RuntimeError(f"Missing OAuth environment for {provider}")
    return {**meta, "client_id":client_id, "client_secret":client_secret, "redirect_uri":redirect}
