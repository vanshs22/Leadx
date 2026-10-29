"""Single source of truth for LeadX agent contracts.

The registry is deliberately kept as plain dictionaries for compatibility with
existing API responses.  The normalization and validation helpers below make
its runtime contract explicit without choosing an executor from request data.
"""
from __future__ import annotations

import importlib
import math
from collections.abc import Mapping, Sequence
from typing import Any


AGENT_REGISTRY: dict[str, dict[str, Any]] = {
 "prospecting":{"name":"Prospecting Agent","description":"Find, qualify and route new prospects using the existing pipeline.","allowed_tools":["discovery","pipeline","crm"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":False,"max_cost":25.0,"max_duration_seconds":900},
 "enrichment":{"name":"Enrichment Agent","description":"Fill missing contact/company fields and preserve provider evidence.","allowed_tools":["enrichment","evidence_graph"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":False,"max_cost":10.0,"max_duration_seconds":600},
 "research":{"name":"Research Agent","description":"Deep-research accounts and decision makers with provenance.","allowed_tools":["research","evidence_graph"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":False,"max_cost":15.0,"max_duration_seconds":600},
 "signal":{"name":"Signal Agent","description":"Turn observed activity, technology and timing clues into explainable signals.","allowed_tools":["signals","scoring","evidence_graph"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":False,"max_cost":8.0,"max_duration_seconds":300},
 "qualification":{"name":"Qualification Agent","description":"Combine fit, intent, contactability and evidence into opportunity intelligence.","allowed_tools":["scoring","crm","evidence_graph"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":False,"max_cost":8.0,"max_duration_seconds":300},
 "outreach":{"name":"Outreach Agent","description":"Prepare evidence-grounded next-best outreach actions and sequence recommendations.","allowed_tools":["ai_copy","approval_inbox","sequences"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":True,"max_cost":12.0,"max_duration_seconds":300},
 "crm":{"name":"CRM Agent","description":"Keep pipeline context, tasks and next-best actions current.","allowed_tools":["crm","tasks"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":False,"max_cost":5.0,"max_duration_seconds":300},
 "deal":{"name":"Deal Agent","description":"Surface deal health, risks, stalled opportunities and next actions.","allowed_tools":["deals","revenue"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":False,"max_cost":8.0,"max_duration_seconds":300},
 "revenue":{"name":"Revenue Analyst","description":"Explain funnel, source ROI and revenue movement.","allowed_tools":["revenue","analytics"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":False,"max_cost":8.0,"max_duration_seconds":300},
 "learning":{"name":"Learning Agent","description":"Learn from won/lost/booked outcomes and produce vertical-specific guidance.","allowed_tools":["outcomes","vertical_priors","evidence_graph"],"input_schema":{"type":"object"},"output_schema":{"type":"object"},"needs_approval":False,"max_cost":8.0,"max_duration_seconds":600},
}

# LeadX 14.0 specialized fabric agents. Existing ten contracts remain unchanged.
_SPECIALIZED = {
 "discovery_planner":("Discovery Agent","Executes the existing exhaustive discovery fabric after prospecting/source planning; preserves all discovery lanes."),
 "source_router":("Provider Intelligence Agent","Selects and ranks executable sources using provider configuration, coverage and learned performance."),
 "identity_resolution":("Identity Resolution Agent","Resolves cross-source company/person duplicates with explainable keys."),
 "contact_finder":("Contact Finder Agent","Finds decision-maker candidates from permitted public/provider evidence."),
 "email_finder":("Email Finder Agent","Builds candidate work-email paths for later verification."),
 "verification":("Verification Agent","Builds consensus verification for contact fields."),
 "web_research":("Web Research Agent","Deep-researches first-party public web surfaces with provenance."),
 "social_intelligence":("Social Intelligence Agent","Normalizes public social discovery evidence."),
 "intent":("Intent Agent","Summarizes buying-intent signals from existing signal sources."),
 "trigger":("Trigger Agent","Identifies actionable business-change triggers."),
 "icp":("ICP Agent","Evaluates account fit against tenant criteria."),
 "personalization":("Personalization Agent","Prepares evidence-backed personalization inputs without sending."),
 "deliverability":("Deliverability Agent","Evaluates contactability and delivery risk before outreach."),
 "compliance":("Compliance Agent","Checks suppression, consent and approval constraints before external actions."),
 "routing":("Routing Agent","Prepares territory/owner routing recommendations."),
 "crm_hygiene":("CRM Hygiene Agent","Finds stale, duplicate and incomplete CRM records."),
 "campaign_optimizer":("Campaign Optimization Agent","Analyzes campaign outcomes and proposes bounded experiments."),
 "data_quality":("Quality Agent","Audits completeness, freshness, provenance and quality-gate readiness."),
 "cost_optimizer":("Cost Optimization Agent","Chooses provider paths by expected cost per successful field."),
 "experiment":("Experiment Agent","Plans measurable provider/waterfall experiments."),
}

_CORE_EXECUTOR_KEYS = {agent_id: f"dedicated:{agent_id}" for agent_id in AGENT_REGISTRY}
_SPECIALIZED_EXECUTOR_KEYS = {agent_id: f"fabric:{agent_id}" for agent_id in _SPECIALIZED}


def _contract(agent_id: str, value: dict[str, Any], executor_key: str) -> dict[str, Any]:
    """Add the explicit contract vocabulary while retaining legacy aliases."""
    out = dict(value)
    out.update({
        "agent_id": agent_id,
        "enabled": True,
        "contract_version": "1",
        "input_contract": dict(out.get("input_schema") or {}),
        "output_contract": dict(out.get("output_schema") or {}),
        "capabilities": list(out.get("allowed_tools") or []),
        "executor_key": executor_key,
        "approval_policy": "required" if out.get("needs_approval") else "none",
        "max_duration": out.get("max_duration_seconds"),
    })
    return out


FULL_AGENT_REGISTRY: dict[str, dict[str, Any]] = {
    agent_id: _contract(agent_id, value, _CORE_EXECUTOR_KEYS[agent_id])
    for agent_id, value in AGENT_REGISTRY.items()
}
FULL_AGENT_REGISTRY["gtm"] = _contract("gtm", {
    "name":"GTM Agent",
    "description":"Owns a GTM mission, decomposes work, delegates to specialized agents, and advances the mission graph without replacing LeadX execution engines.",
    "allowed_tools":["mission","agents","discovery","enrichment","crm"],
    "input_schema":{"type":"object"}, "output_schema":{"type":"object"},
    "needs_approval":False, "max_cost":50.0, "max_duration_seconds":1800,
}, "mission:gtm")
for _id, (_name, _desc) in _SPECIALIZED.items():
    FULL_AGENT_REGISTRY[_id] = _contract(_id, {
        "name":_name, "description":_desc,
        "allowed_tools":["intelligence_fabric","evidence_graph"],
        "input_schema":{"type":"object"}, "output_schema":{"type":"object"},
        "needs_approval":_id in {"personalization","routing"},
        "max_cost":10.0, "max_duration_seconds":600,
    }, _SPECIALIZED_EXECUTOR_KEYS[_id])


class RegistryContractError(ValueError):
    """Raised when the authoritative registry cannot be executed safely."""


def _binding_exists(agent_id: str, executor_key: str) -> bool:
    """Resolve only the trusted local binding; never call an executor/provider."""
    try:
        if executor_key.startswith("dedicated:"):
            bound_agent = executor_key.removeprefix("dedicated:")
            if bound_agent != agent_id or not bound_agent:
                return False
            module = importlib.import_module(f"app.integrations.prod.agent_executors.{bound_agent}")
            return callable(getattr(module, "execute", None))
        if executor_key.startswith("fabric:"):
            bound_agent = executor_key.removeprefix("fabric:")
            if bound_agent != agent_id or not bound_agent:
                return False
            module = importlib.import_module("app.integrations.prod.agent_fabric")
            return bound_agent in getattr(module, "SPECIALIZED", {}) and callable(getattr(module, "execute_specialized", None))
        if executor_key == "mission:gtm":
            if agent_id != "gtm":
                return False
            module = importlib.import_module("app.integrations.prod.agent_missions")
            return callable(getattr(module, "create_mission", None))
    except (ImportError, AttributeError, ModuleNotFoundError):
        return False
    return False


def validate_registry(registry: Mapping[str, dict[str, Any]] | Sequence[tuple[str, dict[str, Any]]] | None = None) -> dict[str, dict[str, Any]]:
    """Validate registry closure and return the validated catalog.

    The normal catalog is a mapping. A sequence of ``(agent_id, contract)``
    pairs is also accepted so tooling can detect duplicate identifiers before
    they are collapsed into a dictionary.
    """
    supplied = FULL_AGENT_REGISTRY if registry is None else registry
    if isinstance(supplied, Mapping):
        catalog = dict(supplied)
        identifiers = list(supplied.keys())
    elif isinstance(supplied, Sequence) and not isinstance(supplied, (str, bytes)):
        pairs = list(supplied)
        if any(not isinstance(pair, (list, tuple)) or len(pair) != 2 for pair in pairs):
            raise RegistryContractError("registry_entries_must_be_id_contract_pairs")
        identifiers = [pair[0] for pair in pairs]
        catalog = dict(pairs) if all(isinstance(agent_id, str) for agent_id in identifiers) else {}
    else:
        raise RegistryContractError("registry_must_be_mapping_or_pairs")
    if any(not isinstance(agent_id, str) or not agent_id for agent_id in identifiers):
        raise RegistryContractError("registry_identifiers_must_be_nonempty_strings")
    if len(identifiers) != len(set(identifiers)):
        raise RegistryContractError("registry_must_contain_unique_entries")
    if len(identifiers) != 31:
        raise RegistryContractError("registry_must_contain_exactly_31_unique_entries")
    for agent_id, contract in catalog.items():
        if not isinstance(agent_id, str) or not agent_id or not isinstance(contract, Mapping):
            raise RegistryContractError(f"invalid_registry_entry:{agent_id}")
        if contract.get("agent_id", agent_id) != agent_id:
            raise RegistryContractError(f"registry_identifier_mismatch:{agent_id}")
        required = ("contract_version", "enabled", "input_contract", "output_contract", "capabilities", "executor_key", "approval_policy", "max_cost", "max_duration")
        missing = [field for field in required if field not in contract]
        if missing:
            raise RegistryContractError(f"missing_registry_contract:{agent_id}:{','.join(missing)}")
        if not isinstance(contract["contract_version"], str) or not contract["contract_version"].strip():
            raise RegistryContractError(f"invalid_contract_version:{agent_id}")
        if not isinstance(contract["enabled"], bool):
            raise RegistryContractError(f"invalid_enabled_state:{agent_id}")
        if not isinstance(contract["input_contract"], Mapping) or contract["input_contract"].get("type") != "object":
            raise RegistryContractError(f"invalid_input_contract:{agent_id}")
        if not isinstance(contract["output_contract"], Mapping) or contract["output_contract"].get("type") != "object":
            raise RegistryContractError(f"invalid_output_contract:{agent_id}")
        if not isinstance(contract["capabilities"], (list, tuple, set)) or not contract["capabilities"]:
            raise RegistryContractError(f"invalid_capabilities:{agent_id}")
        if not isinstance(contract["executor_key"], str) or not contract["executor_key"]:
            raise RegistryContractError(f"missing_executor_binding:{agent_id}")
        if contract["approval_policy"] not in {"none", "required"}:
            raise RegistryContractError(f"invalid_approval_policy:{agent_id}")
        try:
            cost, duration = float(contract["max_cost"]), float(contract["max_duration"])
        except (TypeError, ValueError):
            raise RegistryContractError(f"invalid_bounds:{agent_id}") from None
        if not (math.isfinite(cost) and cost >= 0 and math.isfinite(duration) and duration > 0):
            raise RegistryContractError(f"invalid_bounds:{agent_id}")
        if not contract["enabled"]:
            if not contract.get("disabled_reason"):
                raise RegistryContractError(f"disabled_entry_requires_reason:{agent_id}")
            continue
        if not _binding_exists(agent_id, str(contract["executor_key"])):
            raise RegistryContractError(f"invalid_executor_binding:{agent_id}")
    return dict(catalog)


def resolve_executor_key(agent_id: str, registry: Mapping[str, dict[str, Any]] | None = None) -> str:
    """Return the trusted registry binding; payload fields never participate."""
    catalog = validate_registry(registry)
    try:
        contract = catalog[agent_id]
    except KeyError:
        raise ValueError("unknown_agent") from None
    if not contract["enabled"]:
        raise ValueError("agent_disabled")
    key = str(contract["executor_key"])
    if not _binding_exists(agent_id, key):
        raise RegistryContractError(f"invalid_executor_binding:{agent_id}")
    return key


def get_contract(agent_id: str) -> dict[str, Any]:
    try:
        contract = FULL_AGENT_REGISTRY[agent_id]
    except KeyError:
        raise ValueError("unknown_agent") from None
    if not contract.get("enabled", False):
        raise ValueError("agent_disabled")
    return contract


def validate_input(agent_id: str, input_data: dict | None) -> dict:
    get_contract(agent_id)
    if input_data is None:
        return {}
    if not isinstance(input_data, dict):
        raise ValueError("agent_input_must_be_object")
    return input_data
