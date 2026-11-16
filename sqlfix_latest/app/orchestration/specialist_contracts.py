"""Typed, declarative contracts for GTM specialists.

The contracts describe boundaries and compatibility bindings.  They do not
prove that an executor, provider, or live integration is available.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping

from .catalog import CATALOG, CATALOG_IDS
from .tools import TOOL_NAMES


class SpecialistContractError(ValueError):
    """Raised when a specialist contract would weaken an execution boundary."""


@dataclass(frozen=True)
class SpecialistContract:
    """One specialist's typed input/output and execution policy."""

    specialist_id: str
    responsibility: str
    input_schema: str
    output_schema: str
    input_contract: str
    output_contract: str
    dependencies: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    tenant_boundary: str
    compliance_boundary: str
    executor_key: str
    approval_policy: str
    budget: Mapping[str, int | float | str]
    terminal_behavior: str
    event_driven: bool = False
    activation_events: tuple[str, ...] = ()
    availability: str = "declared_not_tested"

    def __post_init__(self) -> None:
        if not self.specialist_id.strip():
            raise SpecialistContractError("specialist ID is required")
        for name in (
            "responsibility", "input_schema", "output_schema", "input_contract",
            "output_contract", "tenant_boundary", "compliance_boundary",
            "executor_key", "approval_policy", "terminal_behavior",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise SpecialistContractError(f"{self.specialist_id} missing {name}")
        if not self.allowed_tools or any(tool not in TOOL_NAMES for tool in self.allowed_tools):
            raise SpecialistContractError(f"{self.specialist_id} has an unknown typed tool")
        if any(not isinstance(item, str) or not item.strip() for item in self.dependencies):
            raise SpecialistContractError(f"{self.specialist_id} has an invalid dependency")
        if not self.evidence_requirements:
            raise SpecialistContractError(f"{self.specialist_id} needs evidence requirements")
        if self.event_driven and not self.activation_events:
            raise SpecialistContractError(f"{self.specialist_id} needs activation events")
        if self.availability not in {"declared_not_tested", "blocked", "unavailable"}:
            raise SpecialistContractError(f"{self.specialist_id} has an unsupported availability")

    @property
    def tools(self) -> tuple[str, ...]:
        """Compatibility spelling for callers that call tools directly."""
        return self.allowed_tools

    @property
    def evidence(self) -> tuple[str, ...]:
        return self.evidence_requirements

    @property
    def executor(self) -> str:
        return self.executor_key

    @property
    def event_types(self) -> tuple[str, ...]:
        return self.activation_events

    def to_dict(self) -> dict[str, object]:
        return {
            "specialist_id": self.specialist_id,
            "responsibility": self.responsibility,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "input_contract": self.input_contract,
            "output_contract": self.output_contract,
            "dependencies": list(self.dependencies),
            "allowed_tools": list(self.allowed_tools),
            "evidence_requirements": list(self.evidence_requirements),
            "tenant_boundary": self.tenant_boundary,
            "compliance_boundary": self.compliance_boundary,
            "executor_key": self.executor_key,
            "approval_policy": self.approval_policy,
            "budget": dict(self.budget),
            "terminal_behavior": self.terminal_behavior,
            "event_driven": self.event_driven,
            "activation_events": list(self.activation_events),
            "availability": self.availability,
        }


_COMMON_EVIDENCE: Final[tuple[str, ...]] = (
    "evidence_class is preserved",
    "source and observed_at are required for factual fields",
    "static/local/synthetic evidence stays separate from live evidence",
)
_COMMON_TENANT = "authenticated tenant and use-case scope is mandatory"
_COMMON_COMPLIANCE = "execution rechecks authorization, entitlement, suppression, and compliance"
_COMMON_BUDGET: Final[Mapping[str, int | float | str]] = MappingProxyType({"max_attempts": 3, "max_concurrency": 1})

# These descriptions deliberately remain field-level and outcome-specific.  The
# catalog owns IDs and compatibility keys; this module supplies the richer
# contract used by mission and single-task compilation.
_CONTRACT_TEXT: Final[Mapping[str, tuple[str, str]]] = MappingProxyType({
    "gtm": ("objective, selected specialist IDs, dependencies, budgets, gates", "selected subset, handoffs, stop decision, terminal outcome"),
    "prospecting": ("normalized ICP, route cells, source-lane policy", "bounded candidate records with source and route evidence"),
    "enrichment": ("candidate IDs and requested missing or stale fields", "field-level values, provenance, freshness, and missing reasons"),
    "research": ("account scope, research questions, and permitted sources", "evidence-backed account findings and confidence"),
    "signal": ("account scope, signal types, and freshness window", "observed signals with timestamps, source, and confidence"),
    "qualification": ("ICP policy and verified evidence references", "tier, score, decision, missing requirements, and reasons"),
    "outreach": ("approved audience, message inputs, sender, and consent context", "reviewable outreach preparation or an explicitly authorized send result"),
    "crm": ("authorized lead or account projection and mutation intent", "audited CRM projection result and reconciliation reference"),
    "deal": ("authorized deal snapshot and health questions", "deal health findings and bounded next-action recommendation"),
    "revenue": ("authorized revenue facts, window, and aggregation scope", "evidence-backed revenue analysis with limitations"),
    "learning": ("immutable outcome events and evaluation window", "versioned recommendation with sample, confidence, and safety checks"),
    "discovery_planner": ("normalized ICP, geography, route policy, and budgets", "bounded route cells and deterministic stop/replan rules"),
    "source_router": ("route cells, source policy, quota and cost constraints", "ordered eligible lanes and blocked fallback reasons"),
    "identity_resolution": ("normalized entity keys and candidate records", "match decision, confidence, keys, provenance, or merge review"),
    "contact_finder": ("approved entity scope and contact policy", "contact candidates with source and usability evidence"),
    "email_finder": ("approved contact candidates and email policy", "email candidates only, never verified email claims"),
    "verification": ("field/contact candidates and verification policy", "verified, invalid, stale, or unknown field outcomes"),
    "web_research": ("public first-party URLs and research questions", "first-party web evidence with URL, timestamp, and limitations"),
    "social_intelligence": ("permitted public social URLs and signal scope", "public social evidence; URLs remain URLs and never become email"),
    "intent": ("permitted intent sources, account scope, and freshness", "observed intent evidence with confidence and age"),
    "trigger": ("account scope and business-change trigger policy", "dated trigger observations and source evidence"),
    "icp": ("raw ICP, geography, target, and use-case policy", "normalized ICP, sub-ICPs, criteria, and rationale"),
    "personalization": ("approved evidence references and personalization objective", "reviewable personalization inputs linked to evidence"),
    "deliverability": ("verified contact fields, suppression, and delivery context", "contactability and delivery-risk decision with reasons"),
    "compliance": ("authoritative consent, suppression, and policy records", "compliance decision, scope, and missing-policy reason"),
    "routing": ("qualified lead, tenant policy, and ownership rules", "owner/territory recommendation or reservation result"),
    "crm_hygiene": ("authorized CRM records and duplicate/stale rules", "duplicate, stale, incomplete, and recommended-fix findings"),
    "campaign_optimizer": ("approved campaign facts, objective, and experiment bounds", "bounded campaign recommendation; never a send command"),
    "data_quality": ("field evidence, freshness, validity, and readiness policy", "quality/readiness audit with blocking reasons"),
    "cost_optimizer": ("provider policy, historical cost, quota, and quality window", "bounded provider/cost recommendation; never quota reservation"),
    "experiment": ("approved hypothesis, sample, budget, and success criteria", "experiment plan or measured result with rollback target"),
})


def _contract(agent_id: str) -> SpecialistContract:
    entry = CATALOG[agent_id]
    input_contract, output_contract = _CONTRACT_TEXT[agent_id]
    return SpecialistContract(
        specialist_id=agent_id,
        responsibility=entry.responsibility,
        input_schema=entry.input_schema,
        output_schema=entry.output_schema,
        input_contract=input_contract,
        output_contract=output_contract,
        dependencies=entry.dependencies,
        allowed_tools=entry.allowed_tools,
        evidence_requirements=_COMMON_EVIDENCE,
        tenant_boundary=_COMMON_TENANT,
        compliance_boundary=_COMMON_COMPLIANCE,
        executor_key=entry.executor_key,
        approval_policy=entry.approval_policy,
        budget=entry.budget,
        terminal_behavior=(
            "return a typed result with evidence; unavailable or insufficient evidence is terminally labeled"
        ),
        event_driven=agent_id in {"outreach", "crm", "deal", "revenue", "learning", "campaign_optimizer", "cost_optimizer", "experiment"},
        activation_events=(
            "explicit_profile.task_requested", "approval.recorded", "policy_context.available"
        ) if agent_id in {"outreach", "crm", "deal", "revenue", "learning", "campaign_optimizer", "cost_optimizer", "experiment"} else (),
    )


SPECIALIST_CONTRACTS: Final[Mapping[str, SpecialistContract]] = MappingProxyType({
    agent_id: _contract(agent_id) for agent_id in CATALOG_IDS
})

# These capabilities have single-task profile IDs but are intentionally not
# catalog owners.  They are event-driven and cannot be pulled into default lead
# generation by generic growth language.
_EVENT_DRIVEN_SPECS: Final[Mapping[str, tuple[str, str, tuple[str, ...], str, tuple[str, ...]]]] = MappingProxyType({
    "support": ("support request or committed support action", "support recommendation or audited support action", ("support.update",), "support.ticket.requested", ("tenant", "support_authorization", "approval")),
    "customer_success": ("customer outcome and authorized success context", "customer-success recommendation or audited action", ("support.update",), "customer_success.requested", ("tenant", "customer_authorization", "approval")),
    "reliability": ("environment health observations and recovery scope", "health or recovery observation with evidence class", ("reliability.observe",), "reliability.observation.requested", ("tenant", "environment_scope")),
    "benchmark": ("declared methodology, population, comparator, and window", "evidence-classed benchmark result or blocked status", ("benchmark.measure",), "benchmark.requested", ("tenant", "methodology", "evidence_policy")),
    "evidence": ("bounded evidence scope and redaction policy", "safe evidence record or insufficient-evidence result", ("evidence.record",), "evidence.requested", ("tenant", "scope", "redaction")),
})


def _event_contract(specialist_id: str, values: tuple[str, str, tuple[str, ...], str, tuple[str, ...]]) -> SpecialistContract:
    input_contract, output_contract, tools, event, gates = values
    return SpecialistContract(
        specialist_id=specialist_id,
        responsibility=f"Handle {specialist_id.replace('_', ' ')} only after an explicit event.",
        input_schema=f"leadx.orchestration.{specialist_id}.input.v1",
        output_schema=f"leadx.orchestration.{specialist_id}.output.v1",
        input_contract=input_contract,
        output_contract=output_contract,
        dependencies=(),
        allowed_tools=tools,
        evidence_requirements=_COMMON_EVIDENCE,
        tenant_boundary=_COMMON_TENANT,
        compliance_boundary=_COMMON_COMPLIANCE,
        executor_key=f"compatibility.{specialist_id}",
        approval_policy="explicit_profile_and_event_authorization",
        budget=_COMMON_BUDGET,
        terminal_behavior="return a typed recommendation/action result or blocked/unavailable status; never imply success",
        event_driven=True,
        activation_events=(event,),
    )


EVENT_DRIVEN_CONTRACTS: Final[Mapping[str, SpecialistContract]] = MappingProxyType({
    key: _event_contract(key, values) for key, values in _EVENT_DRIVEN_SPECS.items()
})
ALL_SPECIALIST_CONTRACTS: Final[Mapping[str, SpecialistContract]] = MappingProxyType({
    **SPECIALIST_CONTRACTS,
    **EVENT_DRIVEN_CONTRACTS,
})


def get_specialist_contract(specialist_id: str) -> SpecialistContract:
    try:
        return ALL_SPECIALIST_CONTRACTS[specialist_id]
    except KeyError:
        raise SpecialistContractError(f"specialist_unavailable:{specialist_id}") from None


def validate_specialist_contracts(
    contracts: Mapping[str, SpecialistContract] = SPECIALIST_CONTRACTS,
) -> Mapping[str, SpecialistContract]:
    if tuple(contracts) != CATALOG_IDS:
        raise SpecialistContractError("catalog specialist contracts must cover exactly the 31 catalog entries")
    for specialist_id, contract in contracts.items():
        if specialist_id != contract.specialist_id:
            raise SpecialistContractError("specialist contract key mismatch")
        contract.__post_init__()
        if any(dependency not in CATALOG_IDS for dependency in contract.dependencies):
            raise SpecialistContractError(f"{specialist_id} has an unknown catalog dependency")
    return contracts


validate_specialist_contracts()

__all__ = [
    "SpecialistContract", "SpecialistContractError", "SPECIALIST_CONTRACTS",
    "EVENT_DRIVEN_CONTRACTS", "ALL_SPECIALIST_CONTRACTS", "get_specialist_contract",
    "validate_specialist_contracts",
]
