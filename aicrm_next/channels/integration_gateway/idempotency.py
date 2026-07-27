from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Callable


Json = dict[str, Any]

_STORE: dict[str, Json] = {}


def make_idempotency_key(*, operation: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps({"operation": operation, "payload": payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{operation}:{digest}"


def get_or_create(idempotency_key: str, factory: Callable[[], Json]) -> Json:
    if idempotency_key not in _STORE:
        _STORE[idempotency_key] = deepcopy(factory())
    return deepcopy(_STORE[idempotency_key])


def reset_idempotency_store() -> None:
    _STORE.clear()
