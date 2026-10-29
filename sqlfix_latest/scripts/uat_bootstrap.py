#!/usr/bin/env python3
"""Idempotently provision the two small UAT tenants and test fixtures.

The script reads process environment variables only. It never loads the
workstation dotenv file and emits only resource IDs and status fields.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from scripts.uat_config import UatConfigurationError, validate_uat_environment
except ModuleNotFoundError:  # direct script execution
    from uat_config import UatConfigurationError, validate_uat_environment


class UatBootstrapError(RuntimeError):
    pass


def _required(key: str) -> str:
    value = os.getenv(key, "").strip()
    if not value:
        raise UatBootstrapError(f"{key} is required")
    return value


class SupabaseAdmin:
    def __init__(self, base_url: str, service_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
        }

    def request(self, method: str, path: str, *, query: dict[str, str] | None = None, body: Any = None) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urlencode(query)
        request = Request(url, method=method, headers=self.headers)
        if body is not None:
            request.data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            request.add_header("Prefer", "return=representation,resolution=merge-duplicates")
        try:
            with urlopen(request, timeout=20) as response:
                raw = response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            status = getattr(exc, "code", "transport")
            raise UatBootstrapError(f"UAT provider request failed: {method} {path} ({status})") from exc
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UatBootstrapError(f"UAT provider returned invalid JSON: {method} {path}") from exc

    def table_find(self, table: str, filters: dict[str, str]) -> list[dict[str, Any]]:
        query = {key: f"eq.{value}" for key, value in filters.items()}
        query["limit"] = "1"
        data = self.request("GET", f"/rest/v1/{table}", query=query)
        return data if isinstance(data, list) else []

    def upsert(self, table: str, row: dict[str, Any], conflict: str) -> dict[str, Any]:
        data = self.request("POST", f"/rest/v1/{table}", query={"on_conflict": conflict}, body=row)
        rows = data if isinstance(data, list) else []
        if not rows:
            raise UatBootstrapError(f"UAT upsert returned no row: {table}")
        return rows[0]

    def find_or_create_user(self, email: str, password: str) -> str:
        data = self.request("GET", "/auth/v1/admin/users", query={"per_page": "1000"})
        users = data.get("users", []) if isinstance(data, dict) else []
        existing = next((item for item in users if str(item.get("email", "")).lower() == email.lower()), None)
        if existing and existing.get("id"):
            return str(existing["id"])
        try:
            created = self.request(
                "POST", "/auth/v1/admin/users",
                body={"email": email, "password": password, "email_confirm": True, "user_metadata": {"leadx_uat": True}},
            )
        except UatBootstrapError:
            # A concurrent/idempotent create may have won. Re-read without
            # exposing the provider error or the account address.
            data = self.request("GET", "/auth/v1/admin/users", query={"per_page": "1000"})
            users = data.get("users", []) if isinstance(data, dict) else []
            existing = next((item for item in users if str(item.get("email", "")).lower() == email.lower()), None)
            if not existing or not existing.get("id"):
                raise
            return str(existing["id"])
        user_id = created.get("id") if isinstance(created, dict) else None
        if not user_id:
            raise UatBootstrapError("UAT auth user response had no ID")
        return str(user_id)


def provision(admin: SupabaseAdmin) -> dict[str, Any]:
    client_email = _required("LEADX_UAT_CLIENT_EMAIL")
    client_password = _required("LEADX_UAT_CLIENT_PASSWORD")
    admin_email = _required("LEADX_UAT_ADMIN_EMAIL")
    admin_password = _required("LEADX_UAT_ADMIN_PASSWORD")
    client_user = admin.find_or_create_user(client_email, client_password)
    admin_user = admin.find_or_create_user(admin_email, admin_password)

    tenants: dict[str, str] = {}
    users = {"client": client_user, "admin": admin_user}
    for key, name, slug in (
        ("a", "LeadX UAT Client A", "leadx-uat-client-a"),
        ("b", "LeadX UAT Client B", "leadx-uat-client-b"),
    ):
        row = admin.upsert(
            "tenants",
            {"name": name, "slug": slug, "plan": "starter", "leads_per_month_limit": 5, "allow_tier_b": True, "delivery_channel": "portal", "delivery_config": {}, "status": "active"},
            "slug",
        )
        tenants[key] = str(row["id"])

    memberships = {
        "a_client": admin.upsert("tenant_members", {"tenant_id": tenants["a"], "auth_user_id": client_user, "email": client_email, "role": "owner", "status": "active"}, "tenant_id,auth_user_id")["id"],
        "b_admin": admin.upsert("tenant_members", {"tenant_id": tenants["b"], "auth_user_id": admin_user, "email": admin_email, "role": "owner", "status": "active"}, "tenant_id,auth_user_id")["id"],
    }
    platform_admin = admin.upsert(
        "platform_admins", {"user_id": admin_user, "email": admin_email, "role": "admin", "active": True}, "user_id"
    )["user_id"]

    stages: dict[str, list[str]] = {}
    stage_rows = (("new", "New", 0, "#94a3b8"), ("qualified", "Qualified", 1, "#8b5cf6"), ("won", "Won", 2, "#22c55e"))
    for key, tenant_id in tenants.items():
        stages[key] = []
        for slug, name, position, color in stage_rows:
            row = admin.upsert("pipeline_stages", {"tenant_id": tenant_id, "name": name, "slug": slug, "position": position, "color": color}, "tenant_id,slug")
            stages[key].append(str(row["id"]))

    icps: dict[str, str] = {}
    leads: dict[str, str] = {}
    for key, tenant_id in tenants.items():
        existing_icp = admin.table_find("icp_profiles", {"tenant_id": tenant_id, "name": "UAT bounded ICP"})
        icp = existing_icp[0] if existing_icp else admin.upsert(
            "icp_profiles",
            {"tenant_id": tenant_id, "name": "UAT bounded ICP", "vertical": "test services", "geo": ["UAT City"], "keywords": ["uat fixture"], "weights": {}, "exclusivity": "shared", "active": True},
            "id",
        )
        icps[key] = str(icp["id"])
        fingerprint = f"leadx-uat-fixture-{key}"
        existing_lead = admin.table_find("leads_global", {"fingerprint": fingerprint})
        lead = existing_lead[0] if existing_lead else admin.upsert(
            "leads_global",
            {"fingerprint": fingerprint, "name": f"LeadX UAT Fixture {key.upper()}", "name_normalized": f"leadx uat fixture {key}", "address": "UAT Fixture Address", "city": "UAT City", "state": "UT", "website": "https://example.invalid", "website_domain": "example.invalid", "category": "test services", "business_status": "OPERATIONAL", "source": "leadx_uat_fixture", "source_url": "https://example.invalid", "raw_data": {"uat_fixture": True}},
            "fingerprint",
        )
        leads[key] = str(lead["id"])

    return {"tenants": tenants, "users": users, "memberships": memberships, "platform_admin": str(platform_admin), "stages": stages, "icps": icps, "leads": leads}


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision isolated LeadX UAT fixtures")
    parser.add_argument("--dry-run", action="store_true", help="validate markers and print the bounded plan only")
    parser.add_argument("--apply", action="store_true", help="perform idempotent UAT-only provisioning")
    parser.add_argument("--output", type=Path, default=Path("artifacts/uat/bootstrap.json"))
    args = parser.parse_args()
    if args.dry_run == args.apply:
        parser.error("choose exactly one of --dry-run or --apply")
    try:
        validate_uat_environment(require_apply=args.apply, require_live=args.apply)
        if args.dry_run:
            print(json.dumps({"result": "NOT TESTED", "mode": "uat-dry-run", "bounded": True, "mutations": False}, indent=2))
            return 0
        service_key = _required("SUPABASE_SERVICE_KEY")
        result = provision(SupabaseAdmin(_required("SUPABASE_URL"), service_key))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"result": "PASS", "mode": "uat-bootstrap", "ids": result}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"result": "PASS", "mode": "uat-bootstrap", "artifact": str(args.output), "ids": result}, indent=2, sort_keys=True))
        return 0
    except (UatConfigurationError, UatBootstrapError) as exc:
        print(json.dumps({"result": "BLOCKED", "reason": str(exc)}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
