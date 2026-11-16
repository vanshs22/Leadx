"""Typed-tool execution boundary for the orchestration layer.

The runtime is the last policy barrier before a deterministic engine is called.
It validates the exact tool/profile scope, rechecks mutable execution policy,
keeps idempotency local to the supplied repository, validates engine results,
and records only safe attempt metadata.  It never reads configuration or
creates a provider/database client itself.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator, Callable, Mapping, Protocol

from .contracts import canonical_json
from .evidence import EVIDENCE_CLASSES, EvidenceValidationError, ensure_safe_metadata, validate_evidence_class
from .profiles import DEFAULT_PROFILE_ID, ProfileValidationError, resolve_profile
from .tools import DEFAULT_PROFILE_TOOLS, ToolValidationError, get_tool, validate_tool_name
from .engine_adapters import (
    AdapterNotTestedError,
    AdapterUnavailableError,
    EngineAdapters,
    ProviderBlockedError,
)


# ``profiles.py`` owns profile policy; this small map translates a single
# profile capability into its typed tool without creating a second allowlist.
_TOOL_CAPABILITY: dict[str, str] = {
    "intake.validate": "intake",
    "profile.route": "routing",
    "planner.compile": "discovery_planner",
    "plan.validate": "planner",
    "icp.normalize": "icp",
    "research.collect": "research",
    "signals.observe": "signal",
    "discovery.plan_routes": "discovery_planner",
    "discovery.search": "prospecting",
    "discovery.persist_candidate": "prospecting",
    "entity.resolve": "identity_resolution",
    "entity.merge_review": "identity_resolution",
    "enrichment.waterfall": "enrichment",
    "contact.find": "contact_finder",
    "email.find_candidate": "email_finder",
    "contact.verify": "verification",
    "quality.evaluate": "data_quality",
    "qualification.evaluate": "qualification",
    "assignment.reserve": "routing",
    "crm.project": "crm",
    "delivery.reconcile": "crm",
    "pipeline.instrument": "pipeline",
    "evidence.record": "evidence",
    "learning.observe": "learning",
    "learning.evaluate_shadow": "learning",
    "learning.canary": "learning",
    "benchmark.measure": "benchmark",
    "support.update": "support",
    "deal.update": "deal",
    "revenue.analyze": "revenue",
    "campaign.optimize": "campaign_optimizer",
    "reliability.observe": "reliability",
}


class ToolBoundaryError(RuntimeError):
    """An execution-boundary failure with a safe public category/code."""

    def __init__(self, code: str, category: str = "non_retryable") -> None:
        self.code = str(code)
        self.category = str(category)
        super().__init__(self.code)


@dataclass(frozen=True)
class ExecutionContext:
    tenant_id: str
    use_case_id: str
    profile_id: str
    allowed_tools: tuple[str, ...] = ()
    authorized: bool = True
    entitled: bool = True
    spend_remaining: float | None = None
    compliance_allowed: bool = True
    approval_required: bool = False
    approval_granted: bool = True
    provider_allowed: bool = True
    cancellation_requested: bool = False
    cancellation_event: asyncio.Event | None = None
    evidence_class: str = "local"
    environment: str = "offline"
    local_only: bool = False
    request_id: str = ""
    task_id: str = ""
    attempt: int = 1
    idempotency_key: str = ""
    run_id: str = ""
    job_id: str = ""
    region: str = "global"
    vertical: str = "global"
    freshness: str | None = None
    metadata: Mapping[str, Any] = None  # type: ignore[assignment]
    policy_recheck: Callable[[], Mapping[str, Any] | bool] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.allowed_tools, tuple):
            object.__setattr__(self, "allowed_tools", tuple(self.allowed_tools or ()))
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})

    def cancelled(self) -> bool:
        return bool(self.cancellation_requested or (self.cancellation_event and self.cancellation_event.is_set()))


@dataclass(frozen=True)
class ToolExecutionMetadata:
    request_hash: str
    result_ref: str
    tool_name: str
    tenant_id: str
    use_case_id: str
    request_id: str
    task_id: str
    attempt: int
    cost: float
    provider: str
    evidence_class: str
    environment: str
    status: str
    error_code: str | None = None
    error_category: str | None = None
    idempotent_replay: bool = False
    recorded_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolExecutionResult:
    ok: bool
    output: Any = None
    metadata: ToolExecutionMetadata | None = None
    error: Mapping[str, str] | None = None
    status: str = "completed"


class MetadataSink(Protocol):
    def persist_tool_attempt(self, metadata: Mapping[str, Any]) -> None: ...


class IdempotencyStore(Protocol):
    def get(self, key: str) -> ToolExecutionResult | None: ...
    def put(self, key: str, result: ToolExecutionResult) -> None: ...


class InMemoryMetadataSink:
    """Offline sink used by focused tests and local synthetic execution."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def persist_tool_attempt(self, metadata: Mapping[str, Any]) -> None:
        self.records.append(dict(metadata))


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        self.values: dict[str, ToolExecutionResult] = {}

    def get(self, key: str) -> ToolExecutionResult | None:
        return self.values.get(key)

    def put(self, key: str, result: ToolExecutionResult) -> None:
        self.values[key] = result


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(child) for child in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("value is not JSON-safe")


def _reject_secrets(value: Any, path: str = "value") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key).lower().replace("-", "_")
            if name == ".env" or name in {"password", "secret", "credential", "token", "api_key", "access_token", "private_key"} or "bearer" in name:
                raise ToolBoundaryError("schema_invalid", "non_retryable")
            _reject_secrets(child, f"{path}.{key}")
    elif isinstance(value, (tuple, list)):
        for index, child in enumerate(value):
            _reject_secrets(child, f"{path}[{index}]")
    elif isinstance(value, str) and value.lower().startswith("bearer "):
        raise ToolBoundaryError("schema_invalid", "non_retryable")


def _validate_json(value: Any, *, code: str = "schema_invalid") -> None:
    try:
        _jsonable(value)
    except (TypeError, ValueError):
        raise ToolBoundaryError(code, "non_retryable") from None
    _reject_secrets(value)


def _tenant_values(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) == "tenant_id" and child is not None:
                found.append(str(child))
            else:
                found.extend(_tenant_values(child))
    elif isinstance(value, (tuple, list)):
        for child in value:
            found.extend(_tenant_values(child))
    return found


def _result_ref(value: Any) -> str:
    try:
        encoded = canonical_json(_jsonable(value)).encode("utf-8")
    except Exception:
        encoded = f"stream:{type(value).__name__}".encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _request_hash(tool_name: str, payload: Mapping[str, Any], context: ExecutionContext) -> str:
    value = {
        "tool_name": tool_name,
        "input": _jsonable(payload),
        "tenant_id": context.tenant_id,
        "use_case_id": context.use_case_id,
        "profile_id": context.profile_id,
        "request_id": context.request_id,
        "task_id": context.task_id,
    }
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _estimated_cost(payload: Mapping[str, Any]) -> float:
    value = payload.get("estimated_cost", payload.get("cost", payload.get("_estimated_cost", 0.0)))
    try:
        cost = float(value or 0.0)
    except (TypeError, ValueError):
        raise ToolBoundaryError("schema_invalid", "non_retryable") from None
    if cost < 0:
        raise ToolBoundaryError("schema_invalid", "non_retryable")
    return cost


def _safe_error(exc: BaseException, code: str, category: str) -> dict[str, str]:
    # Never include provider text, URLs, request input, or exception messages.
    return {"code": code, "category": category, "type": type(exc).__name__}


class TypedToolRuntime:
    """Execute one allowlisted typed tool after all mutable policy checks."""

    def __init__(
        self,
        *,
        adapters: EngineAdapters | None = None,
        metadata_sink: MetadataSink | Callable[[Mapping[str, Any]], None] | None = None,
        idempotency_store: IdempotencyStore | None = None,
        input_validators: Mapping[str, Callable[[Any], Any]] | None = None,
        output_validators: Mapping[str, Callable[[Any], Any]] | None = None,
    ) -> None:
        self.adapters = adapters or EngineAdapters.defaults()
        self.metadata_sink = metadata_sink or InMemoryMetadataSink()
        self.idempotency_store = idempotency_store or InMemoryIdempotencyStore()
        self.input_validators = dict(input_validators or {})
        self.output_validators = dict(output_validators or {})

    def _persist(self, metadata: ToolExecutionMetadata) -> None:
        record = metadata.to_dict()
        ensure_safe_metadata(record)
        sink = self.metadata_sink
        if callable(sink) and not hasattr(sink, "persist_tool_attempt"):
            sink(record)
        else:
            sink.persist_tool_attempt(record)  # type: ignore[union-attr]

    def _key(self, tool_name: str, context: ExecutionContext, payload: Mapping[str, Any]) -> str:
        supplied = context.idempotency_key or str(payload.get("idempotency_key") or "")
        if not supplied:
            supplied = _request_hash(tool_name, payload, context)
        return ":".join((context.tenant_id, context.use_case_id, tool_name, supplied))

    def _policy(self, tool_name: str, payload: Mapping[str, Any], context: ExecutionContext) -> None:
        try:
            validate_tool_name(tool_name)
            get_tool(tool_name)
            profile = resolve_profile(context.profile_id)
        except (ToolValidationError, ProfileValidationError):
            raise ToolBoundaryError("tool_not_allowlisted", "blocked") from None
        if not context.tenant_id or not context.use_case_id:
            raise ToolBoundaryError("unauthorized", "blocked")
        if not context.authorized:
            raise ToolBoundaryError("unauthorized", "blocked")
        if not context.entitled:
            raise ToolBoundaryError("unentitled", "blocked")
        if not context.compliance_allowed:
            raise ToolBoundaryError("compliance_blocked", "blocked")
        if context.approval_required and not context.approval_granted:
            raise ToolBoundaryError("approval_required", "blocked")
        if context.cancelled():
            raise ToolBoundaryError("cancelled", "terminal")
        try:
            validate_evidence_class(context.evidence_class)
        except EvidenceValidationError:
            raise ToolBoundaryError("schema_invalid", "non_retryable") from None
        if _tenant_values(payload) and any(value != context.tenant_id for value in _tenant_values(payload)):
            raise ToolBoundaryError("cross_tenant_handoff", "blocked")
        if context.allowed_tools:
            allowed = set(context.allowed_tools)
        elif context.profile_id == DEFAULT_PROFILE_ID:
            allowed = set(DEFAULT_PROFILE_TOOLS)
        else:
            allowed = {f"{tool}.evaluate" for tool in ()}  # fail closed below for missions/singles
        profile_tools = set(profile.included_task_types)
        capability = _TOOL_CAPABILITY.get(tool_name)
        profile_allows = tool_name in profile_tools or (capability is not None and capability in profile_tools)
        if tool_name not in allowed or not profile_allows:
            raise ToolBoundaryError("tool_not_allowlisted", "blocked")
        cost = _estimated_cost(payload)
        if context.spend_remaining is not None and cost > float(context.spend_remaining):
            raise ToolBoundaryError("budget_exhausted", "blocked")
        if context.policy_recheck is not None:
            try:
                refreshed = context.policy_recheck()
            except Exception:
                raise ToolBoundaryError("policy_recheck_unavailable", "blocked") from None
            if refreshed is False:
                raise ToolBoundaryError("policy_recheck_failed", "blocked")
            if isinstance(refreshed, Mapping):
                if refreshed.get("tenant_id") not in (None, context.tenant_id):
                    raise ToolBoundaryError("cross_tenant_handoff", "blocked")
                checks = {
                    "authorized": "unauthorized", "entitled": "unentitled",
                    "compliance_allowed": "compliance_blocked", "approval_granted": "approval_required",
                    "provider_allowed": "provider_blocked",
                }
                for field, code in checks.items():
                    if field in refreshed and refreshed[field] is False:
                        raise ToolBoundaryError(code, "blocked")
                if refreshed.get("cancellation_requested") is True:
                    raise ToolBoundaryError("cancelled", "terminal")

    def _input(self, tool_name: str, payload: Any) -> Mapping[str, Any]:
        if not isinstance(payload, Mapping):
            raise ToolBoundaryError("schema_invalid", "non_retryable")
        _validate_json(payload)
        validator = self.input_validators.get(tool_name)
        if validator is not None:
            try:
                valid = validator(payload)
            except Exception:
                raise ToolBoundaryError("schema_invalid", "non_retryable") from None
            if valid is False:
                raise ToolBoundaryError("schema_invalid", "non_retryable")
        return payload

    def _output(self, tool_name: str, output: Any, context: ExecutionContext) -> Any:
        if inspect.isasyncgen(output) or hasattr(output, "__aiter__"):
            return self._stream(tool_name, output, context)
        _validate_json(output)
        if _tenant_values(output) and any(value != context.tenant_id for value in _tenant_values(output)):
            raise ToolBoundaryError("cross_tenant_handoff", "blocked")
        validator = self.output_validators.get(tool_name)
        if validator is not None:
            try:
                valid = validator(output)
            except Exception:
                raise ToolBoundaryError("schema_invalid", "non_retryable") from None
            if valid is False:
                raise ToolBoundaryError("schema_invalid", "non_retryable")
        return output

    async def _stream(self, tool_name: str, output: AsyncIterator[Any], context: ExecutionContext) -> AsyncIterator[Any]:
        async for item in output:
            if context.cancelled():
                raise ToolBoundaryError("cancelled", "terminal")
            yield self._output(tool_name, item, context)

    def _failure(
        self,
        tool_name: str,
        context: ExecutionContext,
        request_hash: str,
        exc: BaseException,
        code: str,
        category: str,
        *,
        cost: float = 0.0,
    ) -> ToolExecutionResult:
        safe_evidence = context.evidence_class if context.evidence_class in EVIDENCE_CLASSES else "local"
        metadata = ToolExecutionMetadata(
            request_hash=request_hash, result_ref="", tool_name=tool_name,
            tenant_id=context.tenant_id, use_case_id=context.use_case_id,
            request_id=context.request_id, task_id=context.task_id, attempt=context.attempt,
            cost=max(0.0, cost), provider="unknown", evidence_class=safe_evidence,
            environment=context.environment, status=category, error_code=code,
            error_category=category, recorded_at=datetime.now(timezone.utc).isoformat(),
        )
        self._persist(metadata)
        return ToolExecutionResult(False, metadata=metadata, error=_safe_error(exc, code, category), status=category)

    async def execute(self, tool_name: str, payload: Mapping[str, Any], context: ExecutionContext) -> ToolExecutionResult:
        request_hash = ""
        estimated_cost = 0.0
        try:
            payload = self._input(tool_name, payload)
            estimated_cost = _estimated_cost(payload)
            request_hash = _request_hash(tool_name, payload, context)
            self._policy(tool_name, payload, context)
            key = self._key(tool_name, context, payload)
            replay = self.idempotency_store.get(key)
            if replay is not None:
                metadata = replay.metadata
                if metadata is not None:
                    replay_metadata = ToolExecutionMetadata(**{**metadata.to_dict(), "idempotent_replay": True})
                    return ToolExecutionResult(replay.ok, replay.output, replay_metadata, replay.error, replay.status)
                return replay
            invocation = await self.adapters.invoke(tool_name, payload, context)
            output = self._output(tool_name, invocation.value, context)
            cost = max(0.0, float(invocation.cost or 0.0))
            provider = str(invocation.provider or "unknown")[:100]
            try:
                evidence_class = validate_evidence_class(str(invocation.evidence_class or context.evidence_class))
            except EvidenceValidationError:
                raise ToolBoundaryError("schema_invalid", "non_retryable") from None
            metadata = ToolExecutionMetadata(
                request_hash=request_hash, result_ref=_result_ref(output), tool_name=tool_name,
                tenant_id=context.tenant_id, use_case_id=context.use_case_id,
                request_id=context.request_id, task_id=context.task_id, attempt=context.attempt,
                cost=cost, provider=provider, evidence_class=evidence_class,
                environment=context.environment, status="completed",
                recorded_at=datetime.now(timezone.utc).isoformat(),
            )
            self._persist(metadata)
            result = ToolExecutionResult(True, output, metadata, status="completed")
            self.idempotency_store.put(key, result)
            return result
        except ToolBoundaryError as exc:
            return self._failure(tool_name, context, request_hash, exc, exc.code, exc.category, cost=estimated_cost)
        except ProviderBlockedError as exc:
            return self._failure(tool_name, context, request_hash, exc, "provider_blocked", "blocked", cost=estimated_cost)
        except AdapterNotTestedError as exc:
            return self._failure(tool_name, context, request_hash, exc, "not_tested", "not_tested", cost=estimated_cost)
        except AdapterUnavailableError as exc:
            return self._failure(tool_name, context, request_hash, exc, "adapter_unavailable", "not_tested", cost=estimated_cost)
        except asyncio.CancelledError as exc:
            return self._failure(tool_name, context, request_hash, exc, "cancelled", "terminal", cost=estimated_cost)
        except (TimeoutError, ConnectionError) as exc:
            return self._failure(tool_name, context, request_hash, exc, "provider_retryable", "retryable", cost=estimated_cost)
        except Exception as exc:
            return self._failure(tool_name, context, request_hash, exc, "tool_failed", "non_retryable", cost=estimated_cost)


# Small functional facade for callers that do not need to retain a runtime.
async def execute_tool(
    tool_name: str,
    payload: Mapping[str, Any],
    context: ExecutionContext,
    *,
    adapters: EngineAdapters | None = None,
    metadata_sink: MetadataSink | Callable[[Mapping[str, Any]], None] | None = None,
    idempotency_store: IdempotencyStore | None = None,
    input_validators: Mapping[str, Callable[[Any], Any]] | None = None,
    output_validators: Mapping[str, Callable[[Any], Any]] | None = None,
) -> ToolExecutionResult:
    return await TypedToolRuntime(
        adapters=adapters, metadata_sink=metadata_sink, idempotency_store=idempotency_store,
        input_validators=input_validators, output_validators=output_validators,
    ).execute(tool_name, payload, context)


ToolRuntime = TypedToolRuntime

__all__ = [
    "ExecutionContext", "IdempotencyStore", "InMemoryIdempotencyStore", "InMemoryMetadataSink",
    "MetadataSink", "ToolBoundaryError", "ToolExecutionMetadata", "ToolExecutionResult",
    "ToolRuntime", "TypedToolRuntime", "execute_tool",
]
