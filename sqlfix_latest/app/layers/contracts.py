from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional

@dataclass(slots=True)
class LayerContext:
    tenant_id: str
    run_id: Optional[str] = None
    icp_id: Optional[str] = None
    vertical: Optional[str] = None
    config: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class LayerResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class LeadCandidate:
    data: dict[str, Any]
    sources: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.data)
