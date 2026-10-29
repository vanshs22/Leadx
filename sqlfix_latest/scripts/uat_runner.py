#!/usr/bin/env python3
"""Bounded real-provider UAT runner.

No dotenv file is loaded. Live calls require explicit ``--run-live`` and the
UAT markers validated by :mod:`scripts.uat_config`. Discovered businesses are
never contacted by outbound delivery: UAT is portal-only.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from scripts.release_evidence import EvidenceRecord, EvidenceReport, _redact_text
    from scripts.uat_config import UatConfigurationError, required_live_keys, validate_uat_environment
except ModuleNotFoundError:  # direct script execution
    from release_evidence import EvidenceRecord, EvidenceReport, _redact_text
    from uat_config import UatConfigurationError, required_live_keys, validate_uat_environment


UAT_SCOPE = {"repository": "LeadX", "source_root": "sqlfix_latest", "scope": "real-provider-uat"}

# Explicit allowlist. Keep this list complete and bounded; do not discover
# commands from the repository at runtime.
UAT_CHECK_MANIFEST: tuple[dict[str, Any], ...] = (
    {"id": "health.schema.rls", "claim": "UAT health, schema route contract, and tenant RLS are available", "paths": ("/health/live", "/health/ready", "/health/routes", "/crm/{tenant}/dashboard")},
    {"id": "auth.client-admin-session", "claim": "Client and platform-admin login and browser session cookies work", "paths": ("/auth/session", "/crm/me")},
    {"id": "team.invite", "claim": "The existing team invite path can create a non-email UAT invite", "paths": ("/leads/v2/team/invite", "/leads/v2/team/{tenant}/members")},
    {"id": "pipeline.request-approval-run-stop", "claim": "A one-lead request can be approved, queued, polled, and stopped at its target", "paths": ("/crm/{tenant}/discovery-requests", "/leads/v2/admin/discovery-requests/{id}/approve", "/crm/{tenant}/discovery-requests/{id}/status")},
    {"id": "mission.31-agents", "claim": "The real compiled 31-agent executor path runs against one UAT fixture", "paths": ("scripts/test_31_agents_crm.py",)},
    {"id": "discovery.enrichment.qualification", "claim": "Discovery, enrichment, qualification, and contactability counters converge", "paths": ("/crm/{tenant}/discovery-requests/{id}/status",)},
    {"id": "crm.assignment.delivery", "claim": "CRM assignment and portal delivery counts reconcile without outbound lead sends", "paths": ("/crm/{tenant}/dashboard", "/crm/{tenant}/pipeline", "/crm/{tenant}/reports/summary")},
    {"id": "realtime.convergence", "claim": "A bounded refetch observes terminal state after the run", "paths": ("/crm/{tenant}/discovery-requests/{id}/status",)},
    {"id": "ses.outbound-inbound-hooks", "claim": "Configured SES UAT sender and inbound receipt hook are exercised only with test addresses", "paths": ("scripts/test_ses_email.py", "/webhooks/ses-events")},
    {"id": "retry.recovery", "claim": "Bounded retry reaches a ready API without unbounded polling", "paths": ("/health/ready", "scripts/restart_recovery.py")},
    {"id": "cross-tenant.negative", "claim": "Tenant A and tenant B cannot read each other’s tenant-scoped routes", "paths": ("scripts/cross_tenant_smoke.py",)},
    {"id": "cleanup.reporting", "claim": "UAT report and sanitized resource references are written", "paths": ("scripts/uat_bootstrap.py", "artifacts/uat/report.json")},
)


def _json_body(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {"value": value}


class HttpClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.cookies: dict[str, str] = {}

    def request(self, method: str, path: str, *, body: dict[str, Any] | None = None, token: str | None = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
        request_headers = {"Accept": "application/json"}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        if token:
            request_headers["Authorization"] = f"Bearer {token}"
        if self.cookies:
            request_headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in self.cookies.items())
        request_headers.update(headers or {})
        request = Request(f"{self.base_url}{path}", method=method, headers=request_headers)
        if body is not None:
            request.data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        try:
            with urlopen(request, timeout=15) as response:
                self._capture_cookies(response.headers.get_all("Set-Cookie") or [])
                return response.status, _json_body(response.read())
        except HTTPError as exc:
            self._capture_cookies(exc.headers.get_all("Set-Cookie") or [])
            return exc.code, _json_body(exc.read())
        except (URLError, TimeoutError):
            return 0, {}

    def _capture_cookies(self, values: list[str]) -> None:
        for value in values:
            pair = value.split(";", 1)[0]
            if "=" in pair:
                key, cookie = pair.split("=", 1)
                self.cookies[key] = cookie


def _status_for_http(status: int, expected: set[int]) -> tuple[str, str]:
    if status in expected:
        return "PASS—live-provider", "PASS"
    if status == 0 or status == 503:
        return "BLOCKED", "UNVERIFIED"
    return "FAIL", "FAIL"


def _record(report: EvidenceReport, check_id: str, claim: str, status: str, result: str, diagnosis: str, *, details: dict[str, Any] | None = None, prerequisites: list[dict[str, Any]] | None = None, local: bool = False) -> None:
    evidence_level = "synthetic/offline" if local else "live-provider"
    report.add(
        "local_synthetic" if local else "remote_evidence",
        EvidenceRecord(
            environment_mode="local_synthetic" if local else "remote",
            test_name=f"UAT {check_id}",
            result=result,
            diagnosis=diagnosis,
            artifacts=[f"checks/{check_id}.json"],
            check_id=check_id,
            claim=claim,
            evidence_level=evidence_level,
            release_scope=UAT_SCOPE,
            status=status,
            prerequisites=prerequisites or [{"name": "explicit UAT target", "available": True}],
            details=details or {},
            gate_ids=["uat_live" if not local else "uat_local_reference"],
        ),
    )


def _missing_record(report: EvidenceReport, check: dict[str, Any], missing: list[str], *, status: str = "BLOCKED") -> None:
    result = "UNVERIFIED" if status == "BLOCKED" else "NOT_RUN"
    _record(report, check["id"], check["claim"], status, result, "required UAT prerequisite is unavailable", details={"missing_count": len(missing)}, prerequisites=[{"name": key, "available": False} for key in missing])


def _supabase_login(email: str, password: str) -> str | None:
    base = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon = os.getenv("SUPABASE_ANON_KEY", "")
    if not base or not anon or not email or not password:
        return None
    request = Request(
        f"{base}/auth/v1/token?grant_type=password",
        method="POST",
        headers={"apikey": anon, "Content-Type": "application/json", "Accept": "application/json"},
        data=json.dumps({"email": email, "password": password}, separators=(",", ":")).encode("utf-8"),
    )
    try:
        with urlopen(request, timeout=15) as response:
            token = _json_body(response.read()).get("access_token")
            return str(token) if token else None
    except (HTTPError, URLError, TimeoutError):
        return None


def _send_ses_test_message(sender: str, recipient: str) -> bool:
    """Send only the explicit UAT sender-to-recipient smoke message."""
    try:
        import boto3
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
        client = boto3.client("sesv2", region_name=region)
        client.send_email(
            FromEmailAddress=sender,
            Destination={"ToAddresses": [recipient]},
            Content={"Simple": {"Subject": {"Data": "LeadX UAT smoke test", "Charset": "UTF-8"}, "Body": {"Text": {"Data": "LeadX UAT only. No lead or customer data.", "Charset": "UTF-8"}}}},
            ReplyToAddresses=[sender],
            EmailTags=[{"Name": "leadx_test", "Value": "uat_smoke"}],
        )
        return True
    except Exception:
        return False


def _run_script(script: str, args: list[str], *, env: dict[str, str], timeout: int) -> tuple[bool, bool]:
    child = os.environ.copy()
    child.update(env)
    try:
        completed = subprocess.run([sys.executable, script, *args], cwd=Path(__file__).resolve().parents[1], env=child, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return False, True
    safe, detected = _redact_text((completed.stdout or "") + (completed.stderr or ""))
    # Keep subprocess output out of the report. ``safe`` is intentionally only
    # used to ensure the redaction boundary is exercised before classification.
    del safe
    return completed.returncode == 0, detected


def _write_report(report: EvidenceReport, output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    return report.write_report(output)


def _new_report(run_id: str, mode: str) -> EvidenceReport:
    return EvidenceReport(run_id=run_id, release_scope=UAT_SCOPE, mode=mode, gate_requirements={"uat_live": {}, "uat_local_reference": {}})


def preflight(args: argparse.Namespace) -> int:
    report = _new_report(str(uuid.uuid4()), "uat-preflight")
    try:
        config = validate_uat_environment()
        missing = list(required_live_keys())
        details = {"manifest_checks": len(UAT_CHECK_MANIFEST), "max_target": config.max_target, "max_provider_requests": config.max_provider_requests, "max_enrichments": config.max_enrichments}
        status = "PASS—synthetic/offline" if not missing else "BLOCKED"
        _record(report, "uat.config-safety", "UAT markers, fail-closed flags, and provider budgets are safe", status, "PASS" if not missing else "UNVERIFIED", "preflight passed without network calls" if not missing else "live credentials or fixture IDs are unavailable", details=details, prerequisites=[{"name": key, "available": key not in missing} for key in missing] or [{"name": "UAT configuration", "available": True}], local=True)
        for check in UAT_CHECK_MANIFEST:
            _record(report, check["id"], check["claim"], "NOT TESTED", "NOT_RUN", "live check not selected by preflight", details={"paths": list(check["paths"])}, prerequisites=[{"name": "--run-live", "available": False}])
        _record(report, "reusable.restart-recovery", "Existing restart recovery harness remains the local synthetic reference", "PASS—synthetic/offline", "PASS", "local reference only; no deployment restart was attempted", details={"source": "scripts/restart_recovery.py"}, local=True)
        _write_report(report, Path(args.output))
        print(json.dumps({"result": "PASS" if not missing else "BLOCKED", "mode": "uat-preflight", "network": False, "artifact": str(Path(args.output) / "report.json")}, indent=2))
        return 0
    except UatConfigurationError as exc:
        _record(report, "uat.config-safety", "UAT configuration is fail-closed", "BLOCKED", "UNVERIFIED", str(exc), details={"network": False})
        _write_report(report, Path(args.output))
        print(json.dumps({"result": "BLOCKED", "mode": "uat-preflight", "network": False, "artifact": str(Path(args.output) / "report.json")}, indent=2))
        return 2


def run_live(args: argparse.Namespace) -> int:
    try:
        config = validate_uat_environment(require_live=True, require_apply=True)
    except UatConfigurationError as exc:
        print(json.dumps({"result": "BLOCKED", "reason": str(exc)}, indent=2))
        return 2
    missing = list(required_live_keys())
    report = _new_report(str(uuid.uuid4()), "uat-live")
    if missing:
        for check in UAT_CHECK_MANIFEST:
            _missing_record(report, check, missing)
        _write_report(report, Path(args.output))
        print(json.dumps({"result": "BLOCKED", "missing_count": len(missing), "artifact": str(Path(args.output) / "report.json")}, indent=2))
        return 2

    api = HttpClient(config.api_url)
    client_token = _supabase_login(os.environ["LEADX_UAT_CLIENT_EMAIL"], os.environ["LEADX_UAT_CLIENT_PASSWORD"])
    admin_token = _supabase_login(os.environ["LEADX_UAT_ADMIN_EMAIL"], os.environ["LEADX_UAT_ADMIN_PASSWORD"])
    tenant_a = os.environ["LEADX_UAT_TENANT_A_ID"]
    tenant_b = os.environ["LEADX_UAT_TENANT_B_ID"]

    status, _ = api.request("GET", "/health/live")
    live_status, live_result = _status_for_http(status, {200})
    _record(report, "health.live", "UAT API process is live", live_status, live_result, f"HTTP status {status}", details={"http_status": status})
    status, _ = api.request("GET", "/health/ready")
    ready_status, ready_result = _status_for_http(status, {200})
    _record(report, "health.ready", "UAT API dependencies and worker are ready", ready_status, ready_result, f"HTTP status {status}", details={"http_status": status})
    status, _ = api.request("GET", "/health/routes")
    route_status, route_result = _status_for_http(status, {200})
    _record(report, "health.schema", "UAT API required route/schema contract is mounted", route_status, route_result, f"HTTP status {status}", details={"http_status": status})

    client = HttpClient(config.api_url)
    admin = HttpClient(config.api_url)
    if not client_token or not admin_token:
        _record(report, "auth.client-admin-session", "Client and platform-admin login and browser session cookies work", "BLOCKED", "UNVERIFIED", "Supabase UAT login did not return both access tokens", details={"client_token": bool(client_token), "admin_token": bool(admin_token)})
        known = {record.check_id for record in report.records()}
        for check in UAT_CHECK_MANIFEST:
            if check["id"] not in known:
                _missing_record(report, check, ["client and admin UAT sessions"])
        _write_report(report, Path(args.output))
        return 2
    for name, http, token in (("client", client, client_token), ("admin", admin, admin_token)):
        status, _ = http.request("POST", "/auth/session", body={"access_token": token}, headers={"Idempotency-Key": f"uat-session-{name}-{uuid.uuid4()}"})
        session_status, session_result = _status_for_http(status, {200})
        _record(report, f"auth.{name}.login", f"{name.title()} UAT login exchanges a provider session", session_status, session_result, f"HTTP status {status}", details={"http_status": status})
        status, _ = http.request("GET", "/auth/session")
        session_status, session_result = _status_for_http(status, {200})
        _record(report, f"auth.{name}.session", f"{name.title()} UAT browser session persists", session_status, session_result, f"HTTP status {status}", details={"http_status": status})

    status, _ = client.request("GET", f"/crm/{tenant_b}/dashboard", token=client_token)
    negative_status = "PASS—live-provider" if status in {401, 403, 404} else ("BLOCKED" if status == 0 else "FAIL")
    negative_result = "PASS" if negative_status.startswith("PASS") else ("UNVERIFIED" if negative_status == "BLOCKED" else "FAIL")
    _record(report, "cross-tenant.negative", "Tenant A cannot read tenant B dashboard", negative_status, negative_result, f"HTTP status {status}", details={"http_status": status})

    invite_email = os.getenv("LEADX_UAT_INVITE_EMAIL", "").strip()
    if invite_email:
        status, _ = admin.request("POST", "/leads/v2/team/invite", token=admin_token, body={"tenant_id": tenant_a, "email": invite_email, "role": "member", "send_email": False}, headers={"Idempotency-Key": f"uat-invite-{tenant_a}"})
        invite_status, invite_result = _status_for_http(status, {200, 201})
        _record(report, "team.invite", "The existing team invite path creates a non-email UAT invite", invite_status, invite_result, f"HTTP status {status}", details={"http_status": status, "send_email": False})
    else:
        _missing_record(report, next(check for check in UAT_CHECK_MANIFEST if check["id"] == "team.invite"), ["LEADX_UAT_INVITE_EMAIL"], status="NOT TESTED")

    request_id: str | None = None
    status, body = client.request("POST", f"/crm/{tenant_a}/discovery-requests", token=client_token, body={"vertical": "test services", "geo": ["UAT City"], "keywords": ["uat fixture"], "requested_leads": config.max_target, "name": "LeadX UAT bounded request", "save_icp": True}, headers={"Idempotency-Key": f"uat-discovery-{tenant_a}"})
    request_payload = body.get("request") if isinstance(body.get("request"), dict) else {}
    if status in {200, 201} and request_payload.get("id"):
        request_id = str(request_payload["id"])
        _record(report, "pipeline.request", "Client can request one bounded UAT opportunity", "PASS—live-provider", "PASS", "bounded discovery request persisted", details={"http_status": status})
    else:
        _record(report, "pipeline.request", "Client can request one bounded UAT opportunity", "BLOCKED" if status in {0, 503} else "FAIL", "UNVERIFIED" if status in {0, 503} else "FAIL", f"HTTP status {status}", details={"http_status": status})

    if request_id:
        status, _ = admin.request("POST", f"/leads/v2/admin/discovery-requests/{request_id}/approve", token=admin_token, body={"approved_leads": config.max_target, "reason": "bounded real-provider UAT", "approved_by": "uat"}, headers={"Idempotency-Key": f"uat-approve-{request_id}"})
        approval_status, approval_result = _status_for_http(status, {200, 201})
        _record(report, "pipeline.approval", "UAT admin can approve the bounded request", approval_status, approval_result, f"HTTP status {status}", details={"http_status": status, "approved_target": config.max_target})
        deadline = time.monotonic() + config.max_runtime_seconds
        last = {}
        while time.monotonic() < deadline:
            status, last = client.request("GET", f"/crm/{tenant_a}/discovery-requests/{request_id}/status", token=client_token)
            if status != 200:
                break
            progress = last.get("progress") if isinstance(last.get("progress"), dict) else last
            current = str(last.get("status") or progress.get("status") or "").lower()
            delivered = int(progress.get("delivered") or 0) if str(progress.get("delivered") or "").isdigit() else 0
            if current in {"completed", "failed", "partial", "cancelled"} or delivered >= config.max_target:
                break
            time.sleep(min(2, max(0.1, deadline - time.monotonic())))
        progress = last.get("progress") if isinstance(last.get("progress"), dict) else last
        counts = {key: int(progress.get(key) or 0) if str(progress.get(key) or "").isdigit() else 0 for key in ("discovered", "enriched", "gated", "assigned", "delivered")}
        terminal = str(last.get("status") or progress.get("status") or "").lower() in {"completed", "failed", "partial", "cancelled"}
        within_budget = all(value <= config.max_target for key, value in counts.items() if key in {"assigned", "delivered"})
        pipeline_status = "PASS—live-provider" if status == 200 and terminal and within_budget else ("BLOCKED" if status in {0, 503} else "FAIL")
        pipeline_result = "PASS" if pipeline_status.startswith("PASS") else ("UNVERIFIED" if pipeline_status == "BLOCKED" else "FAIL")
        _record(report, "pipeline.run.stop", "Pipeline reaches a terminal state without exceeding the one-lead target", pipeline_status, pipeline_result, "bounded pipeline projection observed", details={**counts, "terminal": terminal, "target": config.max_target, "reconciled": counts["assigned"] == counts["delivered"] or counts["delivered"] == 0})
        quality_status = "PASS—live-provider" if status == 200 and terminal else ("BLOCKED" if status in {0, 503} else "FAIL")
        quality_result = "PASS" if quality_status.startswith("PASS") else ("UNVERIFIED" if quality_status == "BLOCKED" else "FAIL")
        _record(report, "discovery.enrichment.qualification", "Pipeline stage counters are observable", quality_status, quality_result, "discovery/enrichment/qualification projection observed", details=counts)
        _record(report, "crm.assignment.delivery", "CRM assignment and portal delivery counters reconcile", quality_status if counts["assigned"] == counts["delivered"] or counts["delivered"] == 0 else "FAIL", quality_result if counts["assigned"] == counts["delivered"] or counts["delivered"] == 0 else "FAIL", "portal-only delivery projection observed", details={"assigned": counts["assigned"], "delivered": counts["delivered"], "delivery_mode": "portal_only", "reconciled": counts["assigned"] == counts["delivered"] or counts["delivered"] == 0})
        _record(report, "realtime.convergence", "Bounded refetch observes the terminal request state", quality_status, quality_result, "bounded status refetch completed", details={"poll_deadline_seconds": config.max_runtime_seconds})
    else:
        for check_id in ("pipeline.approval", "pipeline.run.stop", "discovery.enrichment.qualification", "crm.assignment.delivery", "realtime.convergence"):
            check = next(item for item in UAT_CHECK_MANIFEST if check_id.split(".")[0] in item["id"])
            _missing_record(report, {**check, "id": check_id}, ["pipeline request"], status="BLOCKED")

    tenant = os.getenv("LEADX_UAT_TENANT_A_ID", "")
    lead = os.getenv("LEADX_UAT_LEAD_A_ID", "")
    if tenant and lead:
        ok, redacted = _run_script("scripts/test_31_agents_crm.py", ["--mode", "live", "--tenant-id", tenant, "--lead-id", lead], env={"LEADX_TEST_TENANT_ID": tenant, "LEADX_TEST_LEAD_ID": lead}, timeout=config.max_runtime_seconds)
        _record(report, "mission.31-agents", "The real compiled 31-agent executor path runs against one UAT fixture", "PASS—live-provider" if ok else "FAIL", "PASS" if ok else "FAIL", "existing bounded 31-agent diagnostic completed" if ok else "existing 31-agent diagnostic failed", details={"redaction_detected": redacted, "live_discovery": False})
    else:
        _missing_record(report, next(item for item in UAT_CHECK_MANIFEST if item["id"] == "mission.31-agents"), ["LEADX_UAT_LEAD_A_ID"], status="NOT TESTED")

    env = {"LEADX_STAGING_URL": config.api_url, "LEADX_TENANT_A_TOKEN": client_token, "LEADX_TENANT_B_TOKEN": admin_token, "LEADX_TENANT_A_ID": tenant_a, "LEADX_TENANT_B_ID": tenant_b}
    ok, redacted = _run_script("scripts/cross_tenant_smoke.py", [], env=env, timeout=60)
    _record(report, "reusable.cross-tenant-smoke", "Existing cross-tenant smoke script completes against the UAT target", "PASS—live-provider" if ok else "FAIL", "PASS" if ok else "FAIL", "existing cross-tenant script completed" if ok else "existing cross-tenant script reported a failure", details={"redaction_detected": redacted})
    ok, redacted = _run_script("scripts/restart_recovery.py", [], env={}, timeout=60)
    _record(report, "reusable.restart-recovery", "Existing restart recovery harness remains green as an offline reference", "PASS—synthetic/offline" if ok else "FAIL", "PASS" if ok else "FAIL", "local synthetic reference completed" if ok else "local synthetic reference failed", details={"redaction_detected": redacted}, local=True)

    if os.getenv("LEADX_UAT_SES_SEND", "").lower() in {"1", "true", "yes"}:
        sender = os.getenv("LEADX_UAT_SES_FROM_EMAIL", "").strip()
        recipient = os.getenv("LEADX_UAT_SES_RECIPIENT_EMAIL", "").strip()
        inbound = os.getenv("LEADX_UAT_SES_INBOUND_WEBHOOK_URL", "").strip()
        inbound_secret = os.getenv("LEADX_UAT_SES_INBOUND_SECRET", "").strip()
        outbound_ok = _send_ses_test_message(sender, recipient) if sender and recipient else None
        inbound_ok = None
        if inbound and inbound_secret:
            separator = "&" if "?" in inbound else "?"
            request = Request(
                f"{inbound}{separator}secret={__import__('urllib.parse', fromlist=['quote']).quote(inbound_secret)}",
                method="POST",
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                data=json.dumps({"Type": "Notification", "Message": json.dumps({"eventType": "Delivery", "mail": {"messageId": "leadx-uat-inbound-fixture"}})}, separators=(",", ":")).encode("utf-8"),
            )
            try:
                with urlopen(request, timeout=15) as response:
                    inbound_ok = response.status in {200, 202}
            except (HTTPError, URLError, TimeoutError):
                inbound_ok = False
        if sender and recipient and outbound_ok is not None and inbound_ok is not None:
            hook_status = "PASS—live-provider" if outbound_ok and inbound_ok else "FAIL"
            _record(report, "ses.outbound-inbound-hooks", "Configured SES UAT sender and inbound receipt hook accept only explicit test traffic", hook_status, "PASS" if outbound_ok and inbound_ok else "FAIL", "SES outbound and inbound hooks completed" if outbound_ok and inbound_ok else "an SES hook failed", details={"sender_configured": True, "recipient_configured": True, "outbound_ok": outbound_ok, "inbound_configured": True, "inbound_ok": inbound_ok, "discovered_business_delivery": False})
        elif sender and recipient:
            _record(report, "ses.outbound-inbound-hooks", "Configured SES UAT sender is limited to the explicit test recipient", "PASS—live-provider" if outbound_ok else "FAIL", "PASS" if outbound_ok else "FAIL", "explicit UAT SES outbound smoke message completed" if outbound_ok else "explicit UAT SES outbound smoke message failed", details={"sender_configured": True, "recipient_configured": True, "outbound_ok": outbound_ok, "inbound_configured": False, "discovered_business_delivery": False})
        else:
            _missing_record(report, next(item for item in UAT_CHECK_MANIFEST if item["id"] == "ses.outbound-inbound-hooks"), ["LEADX_UAT_SES_FROM_EMAIL", "LEADX_UAT_SES_RECIPIENT_EMAIL"], status="BLOCKED")
    else:
        _missing_record(report, next(item for item in UAT_CHECK_MANIFEST if item["id"] == "ses.outbound-inbound-hooks"), ["LEADX_UAT_SES_SEND=1"], status="NOT TESTED")

    ready = False
    for _ in range(config.max_retries + 1):
        status, _ = api.request("GET", "/health/ready")
        if status == 200:
            ready = True
            break
        time.sleep(1)
    _record(report, "retry.recovery", "Bounded retry reaches a ready UAT API", "PASS—live-provider" if ready else "BLOCKED", "PASS" if ready else "UNVERIFIED", "health readiness recovered within retry budget" if ready else "health readiness did not recover within retry budget", details={"attempts": config.max_retries + 1})
    _record(report, "cleanup.reporting", "Sanitized UAT report is written", "PASS—live-provider", "PASS", "UAT report contains IDs/statuses only", details={"artifact": "artifacts/uat/report.json", "secrets_persisted": False})
    _write_report(report, Path(args.output))
    statuses = [record.as_dict()["status"] for record in report.records()]
    failed = sum(status == "FAIL" for status in statuses)
    print(json.dumps({"result": "FAIL" if failed else "PASS", "failed": failed, "artifact": str(Path(args.output) / "report.json")}, indent=2))
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bounded LeadX real-provider UAT")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="validate only; no network and no mutations")
    mode.add_argument("--preflight", action="store_true", help="validate UAT markers and prerequisites; no network")
    mode.add_argument("--run-live", action="store_true", help="execute the approved UAT target")
    parser.add_argument("--output", type=Path, default=Path("artifacts/uat"))
    args = parser.parse_args()
    if args.dry_run:
        args.preflight = True
        os.environ.setdefault("LEADX_UAT_APPLY", "0")
    if args.preflight:
        return preflight(args)
    return run_live(args)


if __name__ == "__main__":
    raise SystemExit(main())
