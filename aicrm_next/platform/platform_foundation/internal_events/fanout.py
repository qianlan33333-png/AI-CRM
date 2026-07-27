from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any


FANOUT_MANIFEST_VERSION = "internal-event-fanout/v1"


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def normalize_fanout_consumers(consumers: Iterable[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in consumers:
        consumer_name = str(_value(item, "consumer_name", "") or "").strip()
        consumer_type = str(_value(item, "consumer_type", "projection") or "projection").strip()
        if not consumer_name or not consumer_type:
            raise ValueError("invalid_internal_event_fanout_manifest")
        normalized.append(
            {
                "consumer_name": consumer_name,
                "consumer_type": consumer_type,
                "max_attempts": max(1, int(_value(item, "max_attempts", 5) or 5)),
            }
        )
    normalized.sort(key=lambda item: item["consumer_name"])
    names = [item["consumer_name"] for item in normalized]
    if len(names) != len(set(names)):
        raise ValueError("invalid_internal_event_fanout_manifest")
    return normalized


def build_fanout_manifest(event_type: str, consumers: Iterable[Any]) -> dict[str, Any]:
    normalized_event_type = str(event_type or "").strip()
    if not normalized_event_type:
        raise ValueError("invalid_internal_event_fanout_manifest")
    normalized_consumers = normalize_fanout_consumers(consumers)
    canonical = json.dumps(
        {
            "event_type": normalized_event_type,
            "version": FANOUT_MANIFEST_VERSION,
            "consumers": normalized_consumers,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "version": FANOUT_MANIFEST_VERSION,
        "hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "expected_consumer_count": len(normalized_consumers),
        "consumers": normalized_consumers,
    }


def validate_fanout_manifest(
    event_type: str,
    manifest: dict[str, Any],
    *,
    consumers: Iterable[Any] | None = None,
    require_consumers: bool = True,
) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("consumers"), list):
        raise ValueError("invalid_internal_event_fanout_manifest")
    normalized = normalize_fanout_consumers(manifest["consumers"])
    if require_consumers and not normalized:
        raise ValueError("invalid_internal_event_fanout_manifest")
    expected = build_fanout_manifest(event_type, normalized)
    if str(manifest.get("version") or "").strip() != expected["version"]:
        raise ValueError("invalid_internal_event_fanout_manifest")
    if int(manifest.get("expected_consumer_count") or 0) != expected["expected_consumer_count"]:
        raise ValueError("invalid_internal_event_fanout_manifest")
    if str(manifest.get("hash") or "").strip() != expected["hash"]:
        raise ValueError("internal_event_fanout_manifest_hash_mismatch")
    if consumers is not None and normalize_fanout_consumers(consumers) != normalized:
        raise ValueError("internal_event_fanout_manifest_consumer_mismatch")
    return normalized
