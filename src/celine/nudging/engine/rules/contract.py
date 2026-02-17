from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List

@dataclass(frozen=True)
class FactsContractResult:
    ok: bool
    errors: List[str]
    scenario: str | None
    facts_version: str | None

def validate_facts_contract(facts: Dict[str, Any]) -> FactsContractResult:
    errors: list[str] = []

    facts_version = facts.get("facts_version")
    scenario = facts.get("scenario")

    if not facts_version:
        errors.append("missing facts_version")
    if not scenario:
        errors.append("missing scenario")

    return FactsContractResult(
        ok=len(errors) == 0,
        errors=errors,
        scenario=scenario if isinstance(scenario, str) else None,
        facts_version=facts_version if isinstance(facts_version, str) else None,
    )
