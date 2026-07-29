from __future__ import annotations


def idempotency_key(scope: str, value: str) -> str:
    return f"{scope}:{value}"
