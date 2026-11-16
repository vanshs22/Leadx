"""Exact typed-tool declarations, with no execution bindings or runtime imports."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping


class ToolValidationError(ValueError):
    """Raised for an unknown or malformed typed tool."""


TOOL_NAMES: Final[tuple[str, ...]] = (
    "intake.validate", "profile.route", "planner.compile", "plan.validate", "icp.normalize",
    "research.collect", "signals.observe", "discovery.plan_routes", "discovery.search",
    "discovery.persist_candidate", "entity.resolve", "entity.merge_review", "enrichment.waterfall",
    "contact.find", "email.find_candidate", "contact.verify", "quality.evaluate", "qualification.evaluate",
    "assignment.reserve", "crm.project", "delivery.reconcile", "pipeline.instrument", "evidence.record",
    "learning.observe", "learning.evaluate_shadow", "learning.canary", "benchmark.measure", "support.update",
    "deal.update", "revenue.analyze", "campaign.optimize", "reliability.observe",
)
FORBIDDEN_DEFAULT_TOOLS: Final[tuple[str, ...]] = (
    "outreach.send", "sequence.enroll", "campaign.send", "reply.handle", "lead.recycle", "post_delivery.analyze",
)
DEFAULT_PROFILE_TOOLS: Final[tuple[str, ...]] = (
    "intake.validate", "profile.route", "planner.compile", "plan.validate", "icp.normalize",
    "research.collect", "discovery.plan_routes", "discovery.search", "discovery.persist_candidate",
    "entity.resolve", "enrichment.waterfall", "contact.find", "email.find_candidate", "contact.verify",
    "quality.evaluate", "qualification.evaluate", "assignment.reserve", "crm.project", "delivery.reconcile",
)


@dataclass(frozen=True)
class ToolDeclaration:
    name: str
    input_schema: str
    output_schema: str
    side_effects: tuple[str, ...]
    owner: str
    tenant_scope: str
    authorization: str
    entitlement: str
    spend: str
    compliance: str
    idempotency: str
    timeout_seconds: int
    retry_class: str
    compatibility_binding: str | None = None

    def __post_init__(self) -> None:
        if self.name not in TOOL_NAMES:
            raise ToolValidationError(f"tool_not_allowlisted:{self.name}")
        for field_name in ("input_schema", "output_schema", "owner", "tenant_scope", "authorization", "entitlement", "spend", "compliance", "idempotency", "retry_class"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ToolValidationError(f"tool {self.name} missing {field_name}")
        if not isinstance(self.side_effects, tuple) or any(not isinstance(item, str) or not item for item in self.side_effects):
            raise ToolValidationError(f"tool {self.name} side effects must be explicit")
        if not isinstance(self.timeout_seconds, int) or self.timeout_seconds < 1:
            raise ToolValidationError(f"tool {self.name} timeout must be positive")


def _declaration(name: str, *, owner: str, effects: tuple[str, ...], retry: str = "bounded_retry", binding: str | None = None) -> ToolDeclaration:
    return ToolDeclaration(
        name=name,
        input_schema=f"leadx.tool.{name.replace('.', '_')}.input.v1",
        output_schema=f"leadx.tool.{name.replace('.', '_')}.output.v1",
        side_effects=effects,
        owner=owner,
        tenant_scope="authenticated tenant/use-case scope",
        authorization="execution-time authorization gate",
        entitlement="execution-time entitlement and budget gate",
        spend="declared spend/credit ceiling",
        compliance="execution-time compliance and suppression gate",
        idempotency="scoped request/task/tool idempotency key",
        timeout_seconds=30,
        retry_class=retry,
        compatibility_binding=binding,
    )


_READ_ONLY = ("none",)
_DECLARATIONS: dict[str, ToolDeclaration] = {}
for _name, _owner in (
    ("intake.validate", "orchestration.intake"), ("profile.route", "orchestration.router"),
    ("planner.compile", "orchestration.planner"), ("plan.validate", "orchestration.plan_validator"),
    ("icp.normalize", "icp"), ("research.collect", "research"), ("signals.observe", "signal"),
    ("discovery.plan_routes", "discovery_planner"), ("discovery.search", "discovery"),
    ("discovery.persist_candidate", "discovery"), ("entity.resolve", "identity_resolution"),
    ("entity.merge_review", "identity_resolution"), ("enrichment.waterfall", "enrichment"),
    ("contact.find", "contact_finder"), ("email.find_candidate", "email_finder"),
    ("contact.verify", "verification"), ("quality.evaluate", "data_quality"),
    ("qualification.evaluate", "qualification"), ("assignment.reserve", "routing"),
    ("crm.project", "crm"), ("delivery.reconcile", "crm"), ("pipeline.instrument", "pipeline"),
    ("evidence.record", "evidence"), ("learning.observe", "learning"),
    ("learning.evaluate_shadow", "learning"), ("learning.canary", "learning"),
    ("benchmark.measure", "benchmark"), ("support.update", "support"), ("deal.update", "deal"),
    ("revenue.analyze", "revenue"), ("campaign.optimize", "campaign_optimizer"),
    ("reliability.observe", "reliability"),
):
    _effects = _READ_ONLY
    if _name in {"discovery.persist_candidate", "entity.merge_review", "assignment.reserve", "crm.project", "delivery.reconcile", "pipeline.instrument", "evidence.record", "support.update", "deal.update"}:
        _effects = ("durable_state_write",)
    _DECLARATIONS[_name] = _declaration(_name, owner=_owner, effects=_effects, binding=f"compatibility.{_owner}")

TOOLS: Final[Mapping[str, ToolDeclaration]] = MappingProxyType(_DECLARATIONS)
TYPED_TOOLS = TOOLS


def validate_tool_name(name: str) -> str:
    if not isinstance(name, str) or name not in TOOL_NAMES:
        raise ToolValidationError(f"tool_not_allowlisted:{name}")
    return name


def get_tool(name: str, tools: Mapping[str, ToolDeclaration] = TOOLS) -> ToolDeclaration:
    validate_tool_name(name)
    try:
        return tools[name]
    except KeyError:
        raise ToolValidationError(f"tool_declaration_missing:{name}") from None


def validate_tools(tools: Mapping[str, ToolDeclaration] = TOOLS) -> Mapping[str, ToolDeclaration]:
    if tuple(tools) != TOOL_NAMES:
        raise ToolValidationError("typed tool declarations are not an exact allowlist")
    for name, declaration in tools.items():
        if name != declaration.name:
            raise ToolValidationError("typed tool registry key mismatch")
        declaration.__post_init__()
    if set(DEFAULT_PROFILE_TOOLS) & set(FORBIDDEN_DEFAULT_TOOLS):
        raise ToolValidationError("default profile contains forbidden outbound work")
    return tools


validate_tools()

__all__ = [
    "ToolDeclaration", "ToolValidationError", "TOOL_NAMES", "TOOLS", "TYPED_TOOLS",
    "DEFAULT_PROFILE_TOOLS", "FORBIDDEN_DEFAULT_TOOLS", "get_tool", "validate_tool_name", "validate_tools",
]
