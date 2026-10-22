"""Safe, configurable outbound CRM delivery adapters.

This module is intentionally independent from assignment creation and the legacy
batch delivery channels.  It provides one normalized execution contract for the
five supported CRMs and is usable with injected HTTP clients in tests.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol
from urllib.parse import urljoin

import httpx

CRM_PROVIDERS = ("salesforce", "hubspot", "pipedrive", "close", "gohighlevel")
DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_ERROR_DETAIL = 300

_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|client[_-]?secret|password|secret)(\s*[=:]\s*)[^\s,;&]+"),
    re.compile(r'''(?i)(["']?(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|secret)["']?\s*:\s*["']?)[^\s,"'}]+'''),
    re.compile(r"(?i)([?&](?:api_token|api_key|access_token|token|key)=)[^&\s]+"),
)


class HTTPClient(Protocol):
    async def request(self, method: str, url: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class DeliveryRequest:
    tenant_id: str
    assignment_id: str
    lead_id: str
    provider: str
    stable_event_id: str
    provider_operation_id: str
    lead: Mapping[str, Any]
    assignment: Mapping[str, Any] = field(default_factory=dict)
    mapping: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class DeliveryResult:
    ok: bool
    provider: str
    status_code: int | None = None
    provider_operation_id: str | None = None
    external_id: str | None = None
    retryable: bool = False
    ambiguous: bool = False
    safe_error_code: str | None = None
    safe_error_detail: str | None = None
    retry_after_seconds: int | None = None

    @property
    def terminal(self) -> bool:
        return not self.ok and not self.retryable

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "provider": self.provider,
            "status_code": self.status_code,
            "provider_operation_id": self.provider_operation_id,
            "external_id": self.external_id,
            "retryable": self.retryable,
            "ambiguous": self.ambiguous,
            "safe_error_code": self.safe_error_code,
            "safe_error_detail": self.safe_error_detail,
            "retry_after_seconds": self.retry_after_seconds,
        }


def redact_error(value: Any, limit: int = MAX_ERROR_DETAIL) -> str | None:
    """Return bounded provider detail without credentials or full response bodies."""
    if value is None:
        return None
    text = str(value).replace("\x00", " ")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    text = re.sub(r"(?i)(https?://[^\s]+)", lambda m: m.group(1).split("?", 1)[0], text)
    text = " ".join(text.split())
    return text[:limit] or None


def stable_provider_operation_id(stable_event_id: str, provider: str, payload_hash: str | None = None) -> str:
    """Create the same operation ID for every replay of one provider event."""
    # The outbox stable event is the idempotency boundary. Payload hashes are
    # retained for audit/change detection but must not create a second provider
    # operation for a replay of the same event.
    material = "|".join((str(stable_event_id), str(provider).lower()))
    return f"leadx-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:40]}"


def classify_http(status_code: int, response_text: Any = "", headers: Mapping[str, Any] | None = None) -> dict[str, Any]:
    status = int(status_code)
    if 200 <= status < 300:
        return {"ok": True, "retryable": False}
    retryable = status in {408, 425, 429} or status >= 500
    code = "rate_limited" if status == 429 else "provider_unavailable" if retryable else (
        "auth_failed" if status in {401, 403} else "validation_failed" if 400 <= status < 500 else "provider_error"
    )
    retry_after = None
    if headers:
        raw = headers.get("retry-after") or headers.get("Retry-After")
        try:
            retry_after = max(1, min(3600, int(float(raw)))) if raw is not None else None
        except (TypeError, ValueError):
            retry_after = None
    return {
        "ok": False,
        "retryable": retryable,
        "ambiguous": status >= 500,
        "safe_error_code": code,
        "safe_error_detail": redact_error(f"provider returned HTTP {status}: {response_text}"),
        "retry_after_seconds": retry_after,
    }


def classify_exception(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException)):
        return {"retryable": True, "ambiguous": True, "safe_error_code": "ambiguous_timeout", "safe_error_detail": "provider request timed out"}
    if isinstance(exc, httpx.RequestError):
        return {"retryable": True, "ambiguous": True, "safe_error_code": "provider_unavailable", "safe_error_detail": redact_error(type(exc).__name__)}
    if isinstance(exc, (ValueError, KeyError)):
        return {"retryable": False, "ambiguous": False, "safe_error_code": "connector_unconfigured", "safe_error_detail": redact_error(exc)}
    return {"retryable": False, "ambiguous": False, "safe_error_code": "provider_error", "safe_error_detail": redact_error(type(exc).__name__)}


def _response_json(response: Any) -> dict[str, Any]:
    try:
        value = response.json()
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _response_text(response: Any) -> str:
    try:
        return redact_error(getattr(response, "text", "")) or ""
    except Exception:
        return ""


def _external_id(response: Any, body: Mapping[str, Any]) -> str | None:
    for value in (body.get("id"), body.get("recordId"), body.get("contactId"), body.get("person_id"), body.get("lead_id")):
        if value:
            return str(value)
    for key in ("contact", "person", "lead", "data"):
        nested = body.get(key)
        if isinstance(nested, Mapping) and nested.get("id"):
            return str(nested["id"])
    return None


def _mapped_data(request: DeliveryRequest) -> dict[str, Any]:
    source = dict(request.lead)
    if not request.mapping:
        keys = ("name", "first_name", "last_name", "email", "phone", "phone_e164", "website", "company_name", "domain", "address", "city", "state", "postal_code")
        return {key: source.get(key) for key in keys if source.get(key) not in (None, "")}
    result: dict[str, Any] = {}
    for destination, origin in request.mapping.items():
        if isinstance(origin, str) and origin in source and source[origin] not in (None, ""):
            result[str(destination)] = source[origin]
        elif origin not in (None, "") and not isinstance(origin, str):
            result[str(destination)] = origin
    return result


def _auth_headers(config: Mapping[str, Any], *, bearer: bool = True) -> dict[str, str]:
    token = config.get("access_token") or config.get("api_key")
    if not token:
        raise ValueError("connector credentials are not configured")
    return {"Authorization": f"Bearer {token}" if bearer else str(token), "Content-Type": "application/json"}


def _require(config: Mapping[str, Any], *keys: str) -> None:
    missing = [key for key in keys if not str(config.get(key) or "").strip()]
    if missing:
        raise ValueError(f"connector configuration missing {','.join(missing)}")


class CRMAdapter(Protocol):
    provider: str

    async def send(self, request: DeliveryRequest, config: Mapping[str, Any], client: HTTPClient) -> DeliveryResult: ...


class _BaseAdapter:
    provider = ""

    def _result(self, request: DeliveryRequest, response: Any) -> DeliveryResult:
        body = _response_json(response)
        outcome = classify_http(getattr(response, "status_code", 0), _response_text(response), getattr(response, "headers", None))
        return DeliveryResult(
            ok=bool(outcome["ok"]), provider=self.provider, status_code=getattr(response, "status_code", None),
            provider_operation_id=request.provider_operation_id, external_id=_external_id(response, body),
            retryable=bool(outcome.get("retryable")), ambiguous=bool(outcome.get("ambiguous")),
            safe_error_code=outcome.get("safe_error_code"), safe_error_detail=outcome.get("safe_error_detail"),
            retry_after_seconds=outcome.get("retry_after_seconds"),
        )

    async def _request(self, request: DeliveryRequest, config: Mapping[str, Any], client: HTTPClient, method: str, url: str, **kwargs: Any) -> DeliveryResult:
        try:
            response = await client.request(method, url, **kwargs)
            return self._result(request, response)
        except Exception as exc:
            outcome = classify_exception(exc)
            return DeliveryResult(
                ok=False, provider=self.provider, provider_operation_id=request.provider_operation_id,
                retryable=bool(outcome.get("retryable")), ambiguous=bool(outcome.get("ambiguous")),
                safe_error_code=outcome.get("safe_error_code"), safe_error_detail=outcome.get("safe_error_detail"),
            )

    @staticmethod
    def _idempotency_headers(request: DeliveryRequest) -> dict[str, str]:
        return {"X-LeadX-Idempotency-Key": request.provider_operation_id, "X-LeadX-Operation-Id": request.provider_operation_id}


class SalesforceAdapter(_BaseAdapter):
    provider = "salesforce"

    async def send(self, request: DeliveryRequest, config: Mapping[str, Any], client: HTTPClient) -> DeliveryResult:
        _require(config, "instance_url", "access_token")
        base = str(config["instance_url"]).rstrip("/") + "/"
        api_version = str(config.get("api_version") or "v60.0")
        object_name = str(config.get("object") or "Contact")
        data = _mapped_data(request)
        external_id = config.get("external_record_id")
        external_field = config.get("external_id_field")
        if external_field:
            data[str(external_field)] = request.provider_operation_id
        if external_id:
            method, url = "PATCH", urljoin(base, f"services/data/{api_version}/sobjects/{object_name}/{external_id}")
        elif external_field:
            method, url = "PATCH", urljoin(base, f"services/data/{api_version}/sobjects/{object_name}/{external_field}/{request.provider_operation_id}")
        else:
            method, url = "POST", urljoin(base, f"services/data/{api_version}/sobjects/{object_name}")
        headers = {**_auth_headers(config), **self._idempotency_headers(request)}
        return await self._request(request, config, client, method, url, headers=headers, json=data)


class HubSpotAdapter(_BaseAdapter):
    provider = "hubspot"

    async def send(self, request: DeliveryRequest, config: Mapping[str, Any], client: HTTPClient) -> DeliveryResult:
        _require(config, "access_token")
        object_name = str(config.get("object") or "contacts")
        properties = _mapped_data(request)
        if config.get("idempotency_property"):
            properties[str(config["idempotency_property"])] = request.provider_operation_id
        url = f"{str(config.get('base_url') or 'https://api.hubapi.com').rstrip('/')}/crm/v3/objects/{object_name}"
        headers = {**_auth_headers(config), **self._idempotency_headers(request)}
        return await self._request(request, config, client, "POST", url, headers=headers, json={"properties": properties})


class PipedriveAdapter(_BaseAdapter):
    provider = "pipedrive"

    async def send(self, request: DeliveryRequest, config: Mapping[str, Any], client: HTTPClient) -> DeliveryResult:
        _require(config, "api_key")
        base = str(config.get("base_url") or "https://api.pipedrive.com/v1").rstrip("/")
        resource = str(config.get("resource") or "persons")
        data = _mapped_data(request)
        if config.get("idempotency_field"):
            data[str(config["idempotency_field"])] = request.provider_operation_id
        headers = {**_auth_headers(config), **self._idempotency_headers(request)}
        return await self._request(request, config, client, "POST", f"{base}/{resource}", headers=headers, json=data)


class CloseAdapter(_BaseAdapter):
    provider = "close"

    async def send(self, request: DeliveryRequest, config: Mapping[str, Any], client: HTTPClient) -> DeliveryResult:
        _require(config, "api_key")
        base = str(config.get("base_url") or "https://api.close.com/api/v1").rstrip("/")
        resource = str(config.get("resource") or "lead")
        data = _mapped_data(request)
        data.setdefault("name", request.lead.get("name") or request.lead.get("company_name") or request.lead.get("domain") or "LeadX lead")
        data["leadx_operation_id"] = request.provider_operation_id
        headers = {"Content-Type": "application/json", **self._idempotency_headers(request)}
        # Close uses HTTP basic authentication with the API key as username.
        return await self._request(request, config, client, "POST", f"{base}/{resource}/", headers=headers, auth=(str(config["api_key"]), ""), json=data)


class GoHighLevelAdapter(_BaseAdapter):
    provider = "gohighlevel"

    async def send(self, request: DeliveryRequest, config: Mapping[str, Any], client: HTTPClient) -> DeliveryResult:
        _require(config, "access_token", "location_id")
        base = str(config.get("base_url") or "https://services.leadconnectorhq.com").rstrip("/")
        data = _mapped_data(request)
        data["locationId"] = str(config["location_id"])
        if config.get("idempotency_field"):
            data[str(config["idempotency_field"])] = request.provider_operation_id
        headers = {**_auth_headers(config), "Version": str(config.get("api_version") or "2021-07-28"), **self._idempotency_headers(request)}
        return await self._request(request, config, client, "POST", f"{base}/contacts/", headers=headers, json=data)


ADAPTER_REGISTRY: dict[str, CRMAdapter] = {
    "salesforce": SalesforceAdapter(),
    "hubspot": HubSpotAdapter(),
    "pipedrive": PipedriveAdapter(),
    "close": CloseAdapter(),
    "gohighlevel": GoHighLevelAdapter(),
}


def get_adapter(provider: str) -> CRMAdapter | None:
    return ADAPTER_REGISTRY.get(str(provider or "").lower())


def resolve_tenant_connector(tenant_id: str, provider: str, connection_id: str | None = None) -> dict[str, Any]:
    """Resolve only a tenant-owned connected CRM row; never invent credentials."""
    provider = str(provider or "").lower()
    if provider not in CRM_PROVIDERS:
        return {"state": "unsupported", "provider": provider, "reason": "provider_not_supported"}
    try:
        from app.integrations.prod.connectors import _config, resolve_connection
        row = resolve_connection(str(tenant_id), provider, connection_id=connection_id)
    except Exception:
        return {"state": "unconfigured", "provider": provider, "reason": "connector_unavailable"}
    if not row:
        return {"state": "unconfigured", "provider": provider, "reason": "connector_not_configured"}
    status = str(row.get("status") or "").lower()
    if status in {"disabled", "revoked", "disconnected", "paused"}:
        return {"state": "disabled", "provider": provider, "connection_id": row.get("id"), "reason": "connector_disabled"}
    if status != "connected":
        return {"state": "unconfigured", "provider": provider, "connection_id": row.get("id"), "reason": "connector_not_connected"}
    try:
        config = _config(row)
    except Exception:
        return {"state": "unconfigured", "provider": provider, "connection_id": row.get("id"), "reason": "connector_secret_unavailable"}
    if not isinstance(config, dict) or not config:
        return {"state": "unconfigured", "provider": provider, "connection_id": row.get("id"), "reason": "connector_credentials_missing"}
    if config.get("enabled") is False:
        return {"state": "disabled", "provider": provider, "connection_id": row.get("id"), "reason": "connector_disabled"}
    return {"state": "ready", "provider": provider, "connection_id": row.get("id"), "external_account_id": row.get("external_account_id"), "config": config, "row": row}


async def execute_crm_delivery(
    request: DeliveryRequest,
    config: Mapping[str, Any] | None = None,
    *,
    client: HTTPClient | None = None,
) -> DeliveryResult:
    """Execute one adapter call, returning safe state instead of raising."""
    provider = str(request.provider or "").lower()
    adapter = get_adapter(provider)
    if adapter is None:
        return DeliveryResult(False, provider, provider_operation_id=request.provider_operation_id, safe_error_code="provider_not_supported", safe_error_detail="CRM adapter is not configured")
    provider = adapter.provider
    if not isinstance(config, Mapping) or not config:
        return DeliveryResult(False, provider, provider_operation_id=request.provider_operation_id, safe_error_code="connector_unconfigured", safe_error_detail="CRM connector is not configured")
    if config.get("enabled") is False:
        return DeliveryResult(False, provider, provider_operation_id=request.provider_operation_id, safe_error_code="connector_disabled", safe_error_detail="CRM connector is disabled")
    timeout = config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    try:
        timeout = max(1.0, min(60.0, float(timeout)))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    try:
        if client is not None:
            return await adapter.send(request, config, client)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as http_client:
            return await adapter.send(request, config, http_client)
    except Exception as exc:
        outcome = classify_exception(exc)
        return DeliveryResult(False, provider, provider_operation_id=request.provider_operation_id, retryable=bool(outcome.get("retryable")), ambiguous=bool(outcome.get("ambiguous")), safe_error_code=outcome.get("safe_error_code"), safe_error_detail=outcome.get("safe_error_detail"))


def make_request(*, tenant_id: str, assignment_id: str, lead_id: str, provider: str, stable_event_id: str, lead: Mapping[str, Any], assignment: Mapping[str, Any] | None = None, mapping: Mapping[str, Any] | None = None, payload_hash: str | None = None) -> DeliveryRequest:
    operation_id = stable_provider_operation_id(stable_event_id, provider, payload_hash)
    metadata = dict(assignment or {})
    metadata.setdefault("provider", str(provider).lower())
    return DeliveryRequest(
        tenant_id=str(tenant_id), assignment_id=str(assignment_id), lead_id=str(lead_id),
        provider=str(provider).lower(), stable_event_id=str(stable_event_id), provider_operation_id=operation_id,
        lead=lead, assignment=metadata, mapping=mapping or {},
    )
