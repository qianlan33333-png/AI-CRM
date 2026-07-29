from __future__ import annotations

from .followup import planned_followup_capabilities
from .pulse import planned_pulse_capabilities


class GetAiAssistContractQuery:
    def execute(self) -> dict:
        return {"ok": True, "pulse": planned_pulse_capabilities(), "followup": planned_followup_capabilities()}

    __call__ = execute
