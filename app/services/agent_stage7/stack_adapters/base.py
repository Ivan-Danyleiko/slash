from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Stage7AdapterInput:
    signal_id: int
    base_decision: str
    internal_gate_passed: bool
    contradictions_count: int
    ambiguity_count: int


class Stage7Adapter(Protocol):
    name: str

    def decide(self, payload: Stage7AdapterInput) -> dict[str, Any]:
        ...

