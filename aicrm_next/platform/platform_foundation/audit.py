from __future__ import annotations


def record_audit_event(action: str, target: str) -> dict[str, str]:
    return {"audit_ref": f"fixture:{action}:{target}"}
