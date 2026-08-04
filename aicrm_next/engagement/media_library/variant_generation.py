from __future__ import annotations

from collections import Counter
from threading import Lock

from aicrm_next.platform.shared.resource_admission import ResourceCapacityExhausted, media_binary_admission

from .application import GenerateImageVariantsCommand


_LOCK = Lock()
_INFLIGHT: set[str] = set()
_METRICS: Counter[str] = Counter()


def generate_missing_image_variants(image_id: str) -> None:
    normalized = str(image_id or "").strip()
    if not normalized:
        return
    with _LOCK:
        if normalized in _INFLIGHT:
            _METRICS["deduplicated"] += 1
            return
        _INFLIGHT.add(normalized)
        _METRICS["scheduled"] += 1
    try:
        with media_binary_admission(rollout_key=f"variant-generation:{normalized}"):
            result = GenerateImageVariantsCommand()(normalized)
        with _LOCK:
            _METRICS["completed" if result.get("generated") else "not_found"] += 1
    except ResourceCapacityExhausted:
        with _LOCK:
            _METRICS["capacity_rejected"] += 1
    except Exception:
        with _LOCK:
            _METRICS["failed"] += 1
    finally:
        with _LOCK:
            _INFLIGHT.discard(normalized)


def variant_generation_snapshot() -> dict[str, int]:
    with _LOCK:
        return {"inflight": len(_INFLIGHT), **{key: int(value) for key, value in sorted(_METRICS.items())}}


__all__ = ["generate_missing_image_variants", "variant_generation_snapshot"]
