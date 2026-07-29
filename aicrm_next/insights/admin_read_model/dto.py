from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AdminReadDiagnostics:
    source_status: str
    error_code: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

